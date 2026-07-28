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
    "Free members total",
    "Paid members total",
    "New free members",
    "New paid members",
    "Detected conversions",
    "Removed free members",
    "Removed paid members",
    "Failed records",
    "Runtime seconds",
]


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

    def _load_credentials(self) -> Any:
        """Return valid Google credentials using service account or OAuth."""
        # Prefer service account if a real JSON key file exists.
        creds_path = Path(self.settings.google_sheets_credentials_path)
        if creds_path.is_file():
            logger.debug("Using service account credentials from %s", creds_path)
            return ServiceAccountCredentials.from_service_account_file(
                str(creds_path), scopes=SCOPES
            )

        # Fall back to OAuth 2.0 client credentials.
        client_id = self.settings.google_client_id
        client_secret = self.settings.google_client_secret
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

    def _update_rows(self, updates: list[tuple[int, list[Any]]]) -> None:
        """Batch update rows by 1-based row index."""
        if not updates:
            return
        data = []
        for row_index, values in updates:
            # Cap the range to the number of columns we are writing.
            end_col = chr(ord("A") + len(values) - 1)
            range_spec = f"{self.members_sheet}!A{row_index}:{end_col}{row_index}"
            data.append({"range": range_spec, "values": [values]})
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

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

    def fetch_existing(self) -> tuple[dict[str, MemberState], dict[str, str]]:
        """Read existing members and map member key -> (MemberState, row_number)."""
        self._find_or_create_sheet(self.members_sheet)
        values = self._sheet_values(f"{self.members_sheet}!A:Z")
        if not values or len(values) < 2:
            return {}, {}

        header = [h.lower().strip() for h in values[0]]
        try:
            key_col = header.index("member key")
        except ValueError:
            key_col = None

        existing_states: dict[str, MemberState] = {}
        existing_ids: dict[str, str] = {}  # row number as string for consistency

        try:
            email_col = header.index("email")
        except ValueError:
            email_col = None

        first_name_col = _index_or_none(header, "first name")
        last_name_col = _index_or_none(header, "last name")

        for row_idx, row in enumerate(values[1:], start=2):
            if not row:
                continue
            if key_col is not None:
                key = row[key_col].strip().lower() if key_col < len(row) else ""
            else:
                # Fallback for legacy sheets without a Member key column.
                try:
                    first_col = header.index("first name")
                    last_col = header.index("last name")
                    first = row[first_col].strip() if first_col < len(row) else ""
                    last = row[last_col].strip() if last_col < len(row) else ""
                    key = f"{first}|{last}".lower()
                except ValueError:
                    continue

            if not key or key == "|":
                continue

            email = row[email_col].strip().lower() if email_col is not None and email_col < len(row) else ""

            # Prefer first/last from dedicated columns; fall back to parsing the key.
            if first_name_col is not None and last_name_col is not None:
                first_name = row[first_name_col].strip() if first_name_col < len(row) else ""
                last_name = row[last_name_col].strip() if last_name_col < len(row) else ""
            elif not key.startswith("email:"):
                first_name, _, last_name = key.partition("|")
            else:
                first_name = ""
                last_name = ""

            state = MemberState(email=email, first_name=first_name, last_name=last_name)
            existing_states[key] = state
            existing_ids[key] = str(row_idx)

        logger.info("Found %d existing members in Google Sheets", len(existing_states))
        return existing_states, existing_ids

    def _clear_sheet_data(self, sheet_name: str, include_header: bool = False) -> None:
        """Clear all rows below the header, or the entire sheet if include_header is True."""
        range_spec = f"{sheet_name}!A1:Z" if include_header else f"{sheet_name}!A2:Z"
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=range_spec,
            ).execute()
            logger.info("Cleared %s from %s", "entire sheet" if include_header else "data rows", sheet_name)
        except Exception as exc:
            logger.warning("Could not clear rows from %s: %s", sheet_name, exc)

    def write_members(
        self,
        members: list[MemberState],
        existing_states: dict[str, MemberState],
        existing_ids: dict[str, str],
    ) -> None:
        # Filter the members list if the owner only wants converted members.
        if self.members_filter == "converted":
            members = [m for m in members if m.current_status == "converted"]

        if not members:
            # Clear everything so the sheet does not show stale members.
            self._clear_sheet_data(self.members_sheet, include_header=True)
            logger.info("No members to write to Google Sheets")
            return

        # Identity is now first+last name (with a new Member key column), so
        # old email-keyed rows would never match. Replace mode keeps the sheet
        # in sync with the current state.
        self._clear_sheet_data(self.members_sheet, include_header=True)

        rows = [self._member_to_row(member) for member in members]
        logger.info("Writing %d member rows to Google Sheets", len(rows))

        # Write the fresh header plus the data in chunks to stay below the
        # Google Sheets API request-size limit.
        all_rows = [MEMBER_HEADERS] + rows
        batch_size = 1000
        for i in range(0, len(all_rows), batch_size):
            self._append_rows(self.members_sheet, all_rows[i : i + batch_size])

    def write_daily_metrics(self, metrics: DailyMetrics) -> None:
        self._find_or_create_sheet(self.metrics_sheet)
        current_header = self._sheet_values(f"{self.metrics_sheet}!A1:J1")
        if not current_header or current_header[0] != DAILY_METRICS_HEADERS:
            self._append_rows(self.metrics_sheet, [DAILY_METRICS_HEADERS])
        values = [[
            metrics.date,
            metrics.free_members_total,
            metrics.paid_members_total,
            metrics.new_free_members,
            metrics.new_paid_members,
            metrics.detected_conversions,
            metrics.removed_free_members,
            metrics.removed_paid_members,
            metrics.failed_records,
            metrics.runtime_seconds,
        ]]
        self._append_rows(self.metrics_sheet, values)

    def write_sync_run(self, summary: dict) -> None:
        # For the simplified build, just log the summary. Google Sheets does not
        # have a dedicated SyncRuns sheet by default.
        logger.info("Sync run summary: %s", summary)

    @staticmethod
    def _member_to_row(member: MemberState) -> list[Any]:
        return [
            member.key,
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
