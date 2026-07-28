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
    cleared: list[str] = []

    def fake_sheet_values(range_spec: str) -> list[list[Any]]:
        return [MEMBER_HEADERS]

    def fake_append_rows(sheet: str, rows: list[list[Any]]) -> None:
        appended.append(rows)

    def fake_clear(sheet_name: str, include_header: bool = False) -> None:
        cleared.append(sheet_name)

    monkeypatch.setattr(sink, "_sheet_values", fake_sheet_values)
    monkeypatch.setattr(sink, "_append_rows", fake_append_rows)
    monkeypatch.setattr(sink, "_update_rows", lambda *args, **kwargs: None)
    monkeypatch.setattr(sink, "_clear_sheet_data", fake_clear)

    converted_member = MemberState(email="jane@example.com", first_name="Jane", last_name="Doe")
    converted_member.current_status = "converted"
    free_member = MemberState(email="john.free@example.com", first_name="John", last_name="Free")
    free_member.current_status = "free_only"
    paid_member = MemberState(email="john.paid@example.com", first_name="John", last_name="Paid")
    paid_member.current_status = "paid_only"

    sink.write_members([converted_member, free_member, paid_member], {}, {})

    assert cleared == [sink.members_sheet]
    assert len(appended) == 1
    written_rows = appended[0]
    # The sink now replaces the entire sheet, starting with the header row.
    assert written_rows[0] == MEMBER_HEADERS
    # Data row: member key is the first column, email is the second.
    assert written_rows[1][0] == "jane|doe"
    assert written_rows[1][1] == "jane@example.com"
    assert written_rows[1][2] == "Jane"
