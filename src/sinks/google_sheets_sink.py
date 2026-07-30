"""Google Sheets sink.

This is the default (and only) sink for the simplified build. It stores
member state in a Members sheet and daily metrics in a DailyMetrics sheet,
updating existing members by email and appending new ones.

Authentication supports either a Google service account JSON file or an
OAuth 2.0 client ID/secret pair. The OAuth token is stored locally so the
sync can run unattended after the first authorization.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build

from ..config import Settings
from ..models import DailyMetrics, MemberState
from ..utils import utc_now
from .base import Sink

logger = logging.getLogger("skool_sync")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _index_or_none(header: list[str], column_name: str) -> int | None:
    try:
        return header.index(column_name)
    except ValueError:
        return None


MEMBER_HEADERS = [
    "Member key",
    "Skool member id",
    "Email",
    "First name",
    "Last name",
    "Full name",
    "Free status",
    "Paid status",
    "Free joined at",
    "Paid joined at",
    "First seen free at",
    "First seen paid at",
    "Conversion detected at",
    "Current status",
    "Membership answers",
    "Last synced at",
]

DAILY_METRICS_HEADERS = [
    "Date",
    "Free members",
    "Paid members",
    "Converted members",
    "Removed free members",
    "Removed paid members",
    "Failed records",
    "Runtime seconds",
]

SYNC_RUNS_HEADERS = [
    "Run ID",
    "Started",
    "Finished",
    "Free members",
    "Paid members",
    "Converted members",
    "Removed free members",
    "Removed paid members",
    "Failed records",
    "Runtime seconds",
    "Dry run",
    "Notes",
]

# Google Sheets API batchUpdate limit is 50,000 cells per request.
# Keep chunks below that: 2000 rows * 15 columns = 30,000 cells.
_BATCH_UPDATE_CHUNK_ROWS = 2000


class GoogleSheetsSink(Sink):
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.google_sheets_spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is required")

        creds = self._load_credentials()
        self.service = build("sheets", "v4", credentials=creds)
        self.spreadsheet_id = settings.google_sheets_spreadsheet_id
        self.members_sheet = settings.google_sheets_members_sheet
        self.metrics_sheet = settings.google_sheets_daily_metrics_sheet
        self.members_filter = settings.google_sheets_members_filter.lower()
        # When the owner filters to converted members only, default the sheet
        # name to "converted" so the tab clearly describes its contents. A
        # custom GOOGLE_SHEETS_MEMBERS_SHEET value is still respected.
        if self.members_filter == "converted" and settings.google_sheets_members_sheet == "Members":
            self.members_sheet = "converted"
        else:
            self.members_sheet = settings.google_sheets_members_sheet

        # Populated by fetch_existing() and consumed by write_members().
        self._existing_rows: dict[str, list[Any]] | None = None
        self._existing_ids: dict[str, str] | None = None

    def _load_credentials(self) -> Any:
        """Return valid Google credentials using service account or OAuth."""
        auth_method = self.settings.google_auth_method.lower()
        creds_path = Path(self.settings.google_sheets_credentials_path)

        # Service account is explicitly requested and the JSON key exists.
        if auth_method == "service_account" and creds_path.is_file():
            logger.debug("Using service account credentials from %s", creds_path)
            return ServiceAccountCredentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )

        # OAuth is explicitly requested; use client credentials/refresh token.
        client_id = self.settings.google_client_id
        client_secret = self.settings.google_client_secret
        if auth_method == "oauth" and client_id and client_secret:
            return self._load_oauth_credentials(client_id, client_secret)

        # Backward compatibility: if no explicit auth method is set, prefer a
        # present service-account key file, otherwise fall back to OAuth.
        if creds_path.is_file():
            logger.debug("Using service account credentials from %s", creds_path)
            return ServiceAccountCredentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )
        if client_id and client_secret:
            return self._load_oauth_credentials(client_id, client_secret)

        raise ValueError(
            "Google Sheets credentials are not configured. "
            "Either place a service account JSON at GOOGLE_SHEETS_CREDENTIALS_PATH "
            "or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and run scripts/google_auth.py."
        )

    def _load_oauth_credentials(self, client_id: str, client_secret: str) -> OAuthCredentials:
        """Load existing OAuth token or ask the user to authorize once."""
        refresh_token = self.settings.google_refresh_token
        if refresh_token:
            return OAuthCredentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES,
            )

        token_path = Path(self.settings.google_oauth_token_path)
        token_path.parent.mkdir(parents=True, exist_ok=True)

        if token_path.exists():
            creds = OAuthCredentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    token_path.write_text(creds.to_json(), encoding="utf-8")
                    logger.info("Refreshed Google OAuth token")
                except RefreshError as exc:
                    logger.warning("OAuth refresh failed, re-authorization required: %s", exc)
                    creds = None
            if creds and creds.valid:
                return creds

        # Token missing or invalid. On a headless server we cannot open a browser.
        # Instruct the operator to run the helper script on a machine with a browser.
        raise ValueError(
            f"Google OAuth token not found or expired ({token_path}). "
            "Run 'python scripts/google_auth.py' to authorize this app, "
            "then copy the generated token file to this machine if needed."
        )

    def _sheet_values(self, range_spec: str) -> list[list[Any]]:
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_spec)
            .execute()
        )
        return result.get("values", [])

    def _append_rows(self, sheet: str, rows: list[list[Any]]) -> None:
        if not rows:
            return
        body = {"values": rows}
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet}!A1",
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()

    def _update_range(self, range_spec: str, values: list[list[Any]]) -> None:
        """Overwrite a range with the given values."""
        body = {"values": values}
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_spec,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()

    def _ensure_headers(self, sheet_name: str, headers: list[str]) -> None:
        """Write the header row to A1, overwriting any existing header."""
        self._find_or_create_sheet(sheet_name)
        end_col = self._column_letter(len(headers) - 1)
        self._update_range(f"{sheet_name}!A1:{end_col}1", [headers])

    @staticmethod
    def _column_letter(index: int) -> str:
        """Convert a 0-based column index to an A1-style column letter.

        Supports more than 26 columns (e.g. 27 -> AA).
        """
        letters = []
        while index >= 0:
            letters.append(chr(ord("A") + (index % 26)))
            index = index // 26 - 1
        return "".join(reversed(letters))

    def _batch_update_rows(self, updates: list[tuple[int, list[Any]]]) -> None:
        """Batch-update rows by 1-based row index."""
        if not updates:
            return

        def _to_request(row_index: int, values: list[Any]) -> dict:
            end_col = self._column_letter(len(values) - 1)
            return {
                "values": [values],
                "range": f"{self.members_sheet}!A{row_index}:{end_col}{row_index}",
            }

        for i in range(0, len(updates), _BATCH_UPDATE_CHUNK_ROWS):
            chunk = updates[i : i + _BATCH_UPDATE_CHUNK_ROWS]
            data = [_to_request(row_index, values) for row_index, values in chunk]
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ).execute()
            logger.debug("Updated %d member rows in Google Sheets", len(chunk))

    def _find_or_create_sheet(self, sheet_name: str) -> None:
        """Ensure a sheet with the given name exists in the spreadsheet."""
        try:
            spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            sheet_names = {sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])}
            if sheet_name in sheet_names:
                return
        except Exception as exc:
            logger.warning("Could not list sheets: %s", exc)
        try:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {"title": sheet_name}
                            }
                        }
                    ]
                },
            ).execute()
            logger.info("Created sheet %s", sheet_name)
        except Exception as exc:
            logger.warning("Could not create sheet %s: %s", sheet_name, exc)

    def _get_sheet_id(self, sheet_name: str) -> int | None:
        try:
            spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
            for sheet in spreadsheet.get("sheets", []):
                if sheet["properties"]["title"] == sheet_name:
                    return sheet["properties"]["sheetId"]
        except Exception as exc:
            logger.warning("Could not find sheet id for %s: %s", sheet_name, exc)
        return None

    def _delete_rows(self, sheet_name: str, row_indices: list[int]) -> None:
        """Delete 1-based row indices from a sheet in a single request."""
        if not row_indices:
            return

        sheet_id = self._get_sheet_id(sheet_name)
        if sheet_id is None:
            logger.warning("Could not delete rows, sheet id not found for %s", sheet_name)
            return

        requests = []
        for row_index in row_indices:
            # Google Sheets API uses 0-based indices.
            requests.append(
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_index - 1,
                            "endIndex": row_index,
                        }
                    }
                }
            )

        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": requests},
        ).execute()
        logger.info("Deleted %d stale rows from %s", len(row_indices), sheet_name)

    @staticmethod
    def _member_to_row(member: MemberState) -> list[Any]:
        return [
            member.key,
            member.skool_member_id,
            member.email,
            member.first_name,
            member.last_name,
            member.full_name,
            member.free_status,
            member.paid_status,
            member.free_joined_at,
            member.paid_joined_at,
            member.first_seen_free_at,
            member.first_seen_paid_at,
            member.conversion_detected_at,
            member.current_status,
            json.dumps(member.membership_answers),
            member.last_synced_at,
        ]

    @staticmethod
    def _row_to_member(row: list[Any], header: list[str]) -> MemberState | None:
        """Parse a sheet row back into a MemberState using the column header."""
        lower_header = [h.lower().strip() for h in header]

        def _value(*candidates: str) -> str:
            for candidate in candidates:
                idx = _index_or_none(lower_header, candidate.lower())
                if idx is not None and idx < len(row):
                    val = row[idx]
                    if val is not None:
                        return str(val)
            return ""

        try:
            membership_answers_raw = _value("membership answers")
            membership_answers = json.loads(membership_answers_raw) if membership_answers_raw else {}
        except Exception:
            membership_answers = {}

        return MemberState(
            skool_member_id=_value("skool member id", "skool_member_id"),
            email=_value("email"),
            first_name=_value("first name", "first_name"),
            last_name=_value("last name", "last_name"),
            full_name=_value("full name", "full_name"),
            free_status=_value("free status", "free_status"),
            paid_status=_value("paid status", "paid_status"),
            free_joined_at=_value("free joined at", "free_joined_at"),
            paid_joined_at=_value("paid joined at", "paid_joined_at"),
            first_seen_free_at=_value("first seen free at", "first_seen_free_at"),
            first_seen_paid_at=_value("first seen paid at", "first_seen_paid_at"),
            conversion_detected_at=_value("conversion detected at", "conversion_detected_at"),
            current_status=_value("current status", "current_status"),
            membership_answers=membership_answers,
            last_synced_at=_value("last synced at", "last_synced_at"),
        )

    def fetch_existing(self) -> tuple[dict[str, MemberState], dict[str, str]]:
        """Read existing members and map member key -> (MemberState, row_number)."""
        self._find_or_create_sheet(self.members_sheet)
        values = self._sheet_values(f"{self.members_sheet}!A:Z")
        if not values:
            self._existing_rows = {}
            self._existing_ids = {}
            return {}, {}

        if len(values) < 2:
            self._existing_rows = {}
            self._existing_ids = {}
            return {}, {}

        header = values[0]
        lower_header = [h.lower().strip() for h in header]

        try:
            key_col = lower_header.index("member key")
        except ValueError:
            key_col = None

        existing_states: dict[str, MemberState] = {}
        self._existing_rows = {}
        self._existing_ids = {}

        for row_idx, row in enumerate(values[1:], start=2):
            if not row:
                continue

            state = self._row_to_member(row, header) or MemberState()
            # Use the current key derivation (member id first, then name/email).
            # The sink preserves the raw "Member key" value, so legacy name- or
            # email-keyed rows are migrated to member-id keys on the next write.
            key = state.key
            if not key:
                continue

            existing_states[key] = state
            self._existing_rows[key] = row
            self._existing_ids[key] = str(row_idx)

        logger.info("Found %d existing members in Google Sheets", len(existing_states))
        return existing_states, self._existing_ids

    def write_members(
        self,
        members: list[MemberState],
        existing_states: dict[str, MemberState],
        existing_ids: dict[str, str],
    ) -> None:
        # write_members expects fetch_existing() to have been called first,
        # but be defensive in case it is ever invoked directly.
        if self._existing_rows is None or self._existing_ids is None:
            self.fetch_existing()

        # Filter the members list if the owner only wants converted members.
        if self.members_filter == "converted":
            members = [m for m in members if m.current_status == "converted"]

        written_keys = {m.key for m in members}

        # Ensure the header row is present before writing data.
        self._ensure_headers(self.members_sheet, MEMBER_HEADERS)

        updates: list[tuple[int, list[Any]]] = []
        appends: list[list[Any]] = []

        for member in members:
            new_row = self._member_to_row(member)

            if member.key in self._existing_ids:
                existing_row = self._existing_rows.get(member.key, [])
                # Only update if the managed columns changed.
                if existing_row[: len(new_row)] != new_row:
                    row_index = int(self._existing_ids[member.key])
                    updates.append((row_index, new_row))
            else:
                appends.append(new_row)

        # 1) Apply updates first (row indices are stable).
        if updates:
            logger.info("Updating %d existing member rows in Google Sheets", len(updates))
            self._batch_update_rows(updates)

        # 2) Append new rows at the bottom.
        if appends:
            logger.info("Appending %d new member rows to Google Sheets", len(appends))
            for i in range(0, len(appends), _BATCH_UPDATE_CHUNK_ROWS):
                chunk = appends[i : i + _BATCH_UPDATE_CHUNK_ROWS]
                self._append_rows(self.members_sheet, chunk)

        # 3) For the converted-only filter, delete rows that are no longer converted.
        if self.members_filter == "converted" and self._existing_ids:
            stale_row_indices = [
                int(self._existing_ids[key])
                for key in self._existing_ids
                if key not in written_keys
            ]
            if stale_row_indices:
                # Delete from largest to smallest so indices stay valid,
                # but the API handles single-row deletions in one request.
                stale_row_indices.sort(reverse=True)
                self._delete_rows(self.members_sheet, stale_row_indices)

        if not members:
            logger.info("No members to write to Google Sheets")

    def write_daily_metrics(self, metrics: DailyMetrics) -> None:
        self._ensure_headers(self.metrics_sheet, DAILY_METRICS_HEADERS)
        values = [[
            metrics.date,
            metrics.free_members_total,
            metrics.paid_members_total,
            metrics.converted_members,
            metrics.removed_free_members,
            metrics.removed_paid_members,
            metrics.failed_records,
            metrics.runtime_seconds,
        ]]
        self._append_rows(self.metrics_sheet, values)

    def write_sync_run(self, summary: dict) -> None:
        """Append a sync run record to the SyncRuns sheet."""
        self._ensure_headers("SyncRuns", SYNC_RUNS_HEADERS)

        started_at = summary.get("started_at")
        finished_at = summary.get("finished_at")
        if isinstance(started_at, str):
            started_at_str = started_at
        else:
            started_at_str = started_at.isoformat() if started_at else ""

        if isinstance(finished_at, str):
            finished_at_str = finished_at
        else:
            finished_at_str = finished_at.isoformat() if finished_at else ""

        notes = " | ".join(summary.get("notes", [])) if isinstance(summary.get("notes"), list) else ""

        row = [
            summary.get("run_id", ""),
            started_at_str,
            finished_at_str,
            summary.get("free_members_total", 0),
            summary.get("paid_members_total", 0),
            summary.get("converted_members", 0),
            summary.get("removed_free_members", 0),
            summary.get("removed_paid_members", 0),
            summary.get("failed_records", 0),
            summary.get("runtime_seconds", 0.0),
            str(summary.get("dry_run", False)),
            notes,
        ]
        self._append_rows("SyncRuns", [row])

