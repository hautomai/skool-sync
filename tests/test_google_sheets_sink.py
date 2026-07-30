"""Tests for GoogleSheetsSink credential handling."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.config import Settings
from src.models import MemberState
from src.sinks.google_sheets_sink import GoogleSheetsSink, MEMBER_HEADERS, SCOPES


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Make tests independent of any real .env file.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "")
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "./data/nonexistent.json")
    return Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
        google_sheets_spreadsheet_id="test-id",
    )


def test_sink_prefers_service_account_when_json_exists(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    """If the service-account JSON exists, the sink uses it."""
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text('{"type": "service_account"}', encoding="utf-8")

    settings.google_sheets_credentials_path = creds_path  # type: ignore[assignment]

    fake_creds = MagicMock()
    from_service_account_file = MagicMock(return_value=fake_creds)
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.ServiceAccountCredentials.from_service_account_file",
        from_service_account_file,
    )
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.build",
        MagicMock(return_value=MagicMock()),
    )

    sink = GoogleSheetsSink(settings)

    from_service_account_file.assert_called_once_with(str(creds_path), scopes=SCOPES)
    assert sink.spreadsheet_id == "test-id"


def test_sink_falls_back_to_oauth_token_when_present(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    """If no service-account JSON exists but an OAuth token file exists, use it."""
    token_path = tmp_path / "token.json"
    token_path.write_text('{"token": "fake"}', encoding="utf-8")

    settings.google_sheets_credentials_path = tmp_path / "missing.json"  # type: ignore[assignment]
    settings.google_client_id = "client-id"
    settings.google_client_secret = "client-secret"
    settings.google_oauth_token_path = token_path  # type: ignore[assignment]

    fake_creds = MagicMock(valid=True, expired=False, refresh_token="refresh")
    from_authorized_user_file = MagicMock(return_value=fake_creds)
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.OAuthCredentials.from_authorized_user_file",
        from_authorized_user_file,
    )
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.build",
        MagicMock(return_value=MagicMock()),
    )

    sink = GoogleSheetsSink(settings)

    from_authorized_user_file.assert_called_once_with(str(token_path), SCOPES)
    assert sink.spreadsheet_id == "test-id"


def test_sink_ignores_directory_service_account_path(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    """A directory (or empty) credentials path should fall through to OAuth."""
    settings.google_sheets_credentials_path = tmp_path  # type: ignore[assignment]
    settings.google_client_id = "client-id"
    settings.google_client_secret = "client-secret"
    settings.google_oauth_token_path = tmp_path / "token.json"  # type: ignore[assignment]
    settings.google_oauth_token_path.write_text('{"token": "fake"}', encoding="utf-8")

    fake_creds = MagicMock(valid=True, expired=False, refresh_token="refresh")
    from_authorized_user_file = MagicMock(return_value=fake_creds)
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.OAuthCredentials.from_authorized_user_file",
        from_authorized_user_file,
    )
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.build",
        MagicMock(return_value=MagicMock()),
    )

    sink = GoogleSheetsSink(settings)

    from_authorized_user_file.assert_called_once_with(str(settings.google_oauth_token_path), SCOPES)
    assert sink.spreadsheet_id == "test-id"


def test_sink_raises_when_no_auth_configured(settings: Settings) -> None:
    """The sink raises when neither service account nor OAuth credentials are set."""
    settings.google_sheets_credentials_path = Path("./data/nonexistent.json")  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Google Sheets credentials are not configured"):
        GoogleSheetsSink(settings)


def test_refresh_token_bypasses_token_file(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """If GOOGLE_REFRESH_TOKEN is set, the sink can authenticate without a token file."""
    settings.google_sheets_credentials_path = Path("./data/nonexistent.json")  # type: ignore[assignment]
    settings.google_client_id = "client-id"
    settings.google_client_secret = "client-secret"
    settings.google_refresh_token = "refresh-token"

    captured: dict[str, Any] = {}

    def fake_build(service_name: str, version: str, credentials: Any) -> Any:
        captured["credentials"] = credentials
        return MagicMock()

    monkeypatch.setattr("src.sinks.google_sheets_sink.build", fake_build)

    sink = GoogleSheetsSink(settings)

    assert sink is not None
    assert captured["credentials"].refresh_token == "refresh-token"


def test_write_members_filtered_to_converted(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    """When the members filter is 'converted', only converted members are written."""
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text('{"type": "service_account"}', encoding="utf-8")
    settings.google_sheets_credentials_path = creds_path  # type: ignore[assignment]
    settings.google_sheets_members_filter = "converted"  # type: ignore[assignment]

    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.ServiceAccountCredentials.from_service_account_file",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "src.sinks.google_sheets_sink.build",
        MagicMock(return_value=MagicMock()),
    )

    sink = GoogleSheetsSink(settings)

    appended: list[list[list[Any]]] = []
    updated: list[list[tuple[int, list[Any]]]] = []
    deleted_rows: list[list[int]] = []
    headers_written: list[tuple[str, list[str]]] = []

    def fake_append_rows(sheet: str, rows: list[list[Any]]) -> None:
        appended.append(rows)

    def fake_batch_update_rows(updates: list[tuple[int, list[Any]]]) -> None:
        updated.append(updates)

    def fake_delete_rows(sheet_name: str, row_indices: list[int]) -> None:
        deleted_rows.append(row_indices)

    def fake_ensure_headers(sheet_name: str, headers: list[str]) -> None:
        headers_written.append((sheet_name, headers))

    monkeypatch.setattr(sink, "_append_rows", fake_append_rows)
    monkeypatch.setattr(sink, "_batch_update_rows", fake_batch_update_rows)
    monkeypatch.setattr(sink, "_delete_rows", fake_delete_rows)
    monkeypatch.setattr(sink, "_ensure_headers", fake_ensure_headers)

    # Simulate that fetch_existing() has already run on an empty sheet.
    sink._existing_rows = {}
    sink._existing_ids = {}

    converted_member = MemberState(email="jane@example.com", first_name="Jane", last_name="Doe")
    converted_member.current_status = "converted"
    free_member = MemberState(email="john.free@example.com", first_name="John", last_name="Free")
    free_member.current_status = "free_only"
    paid_member = MemberState(email="john.paid@example.com", first_name="John", last_name="Paid")
    paid_member.current_status = "paid_only"

    sink.write_members([converted_member, free_member, paid_member], {}, {})

    # Headers are written once, then only the converted member is appended.
    assert headers_written == [(sink.members_sheet, MEMBER_HEADERS)]
    assert len(appended) == 1
    assert len(appended[0]) == 1
    assert appended[0][0][0] == "jane|doe"
    assert appended[0][0][1] == "jane@example.com"
    assert appended[0][0][2] == "Jane"
    assert not updated
    assert not deleted_rows
