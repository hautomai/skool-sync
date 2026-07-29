from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.exporters.apify_exporter import ApifySkoolExporter


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        free_community_url="https://www.skool.com/free-community",
        paid_community_url="https://www.skool.com/paid-community",
        apify_api_token="test-token",
        skool_email="admin@example.com",
        skool_password="secret",
        skool_cookies_path=tmp_path / "cookies.json",
        skool_cookies_refresh_hours=24,
    )


@pytest.fixture
def exporter(settings: Settings) -> ApifySkoolExporter:
    return ApifySkoolExporter(settings)


async def _fake_actor_call(run_input: dict, responses: dict) -> MagicMock:
    action = run_input.get("action")
    return responses.get(action, MagicMock(default_dataset_id="ds-empty"))


async def _fake_dataset_items(dataset_id: str, items_by_dataset: dict) -> list:
    return items_by_dataset.get(dataset_id, [])


@pytest.mark.asyncio
async def test_export_uses_cached_cookies(exporter: ApifySkoolExporter, tmp_path: Path) -> None:
    exporter._cookie_manager.save([{"name": "auth", "value": "cookie123"}])

    captured: dict = {}

    async def fake_call(run_input: dict) -> MagicMock:
        captured["input"] = run_input
        return MagicMock(default_dataset_id="ds-list")

    async def fake_items(dataset_id: str) -> list:
        if dataset_id == "ds-list":
            return [{"email": "a@b.com", "firstName": "A", "lastName": "B"}]
        return []

    exporter._run_actor = fake_call  # type: ignore[assignment]
    exporter._fetch_dataset_items = fake_items  # type: ignore[assignment]

    output = tmp_path / "out.csv"
    await exporter.export_members("https://www.skool.com/group-1", "free", output)

    assert captured["input"]["action"] == "members:list"
    assert captured["input"]["cookies"] == [{"name": "auth", "value": "cookie123"}]
    assert "email" not in captured["input"]
    assert "password" not in captured["input"]


@pytest.mark.asyncio
async def test_export_falls_back_to_credentials_when_no_cookies(exporter: ApifySkoolExporter, tmp_path: Path) -> None:
    captured: dict = {}

    async def fake_call(run_input: dict) -> MagicMock:
        captured["input"] = run_input
        return MagicMock(default_dataset_id="ds-list")

    async def fake_items(dataset_id: str) -> list:
        if dataset_id == "ds-list":
            return [{"email": "a@b.com", "firstName": "A", "lastName": "B"}]
        return []

    exporter._run_actor = fake_call  # type: ignore[assignment]
    exporter._fetch_dataset_items = fake_items  # type: ignore[assignment]

    output = tmp_path / "out.csv"
    await exporter.export_members("https://www.skool.com/group-1", "free", output)

    assert captured["input"]["action"] == "members:list"
    assert captured["input"]["email"] == "admin@example.com"
    assert captured["input"]["password"] == "secret"


@pytest.mark.asyncio
async def test_export_refreshes_cookies_and_uses_them(exporter: ApifySkoolExporter, tmp_path: Path) -> None:
    captured_calls: list = []

    async def fake_call(run_input: dict) -> MagicMock:
        captured_calls.append(run_input)
        action = run_input.get("action")
        if action == "auth:login":
            return MagicMock(default_dataset_id="ds-login")
        return MagicMock(default_dataset_id="ds-list")

    async def fake_items(dataset_id: str) -> list:
        if dataset_id == "ds-login":
            return [{"success": True, "cookies": [{"name": "auth", "value": "fresh"}]}]
        return [{"email": "a@b.com", "firstName": "A", "lastName": "B"}]

    exporter._run_actor = fake_call  # type: ignore[assignment]
    exporter._fetch_dataset_items = fake_items  # type: ignore[assignment]

    output = tmp_path / "out.csv"
    await exporter.export_members("https://www.skool.com/group-1", "free", output)

    assert captured_calls[0]["action"] == "auth:login"
    assert captured_calls[1]["action"] == "members:list"
    assert captured_calls[1]["cookies"] == [{"name": "auth", "value": "fresh"}]


@pytest.mark.asyncio
async def test_export_retries_on_auth_failure_with_cookies(exporter: ApifySkoolExporter, tmp_path: Path) -> None:
    exporter._cookie_manager.save([{"name": "auth", "value": "stale"}])

    captured_calls: list = []
    dataset_state = {"attempt": 0}

    async def fake_call(run_input: dict) -> MagicMock:
        captured_calls.append(run_input)
        action = run_input.get("action")
        if action == "auth:login":
            return MagicMock(default_dataset_id="ds-login")
        # members:list
        dataset_state["attempt"] += 1
        if dataset_state["attempt"] == 1:
            return MagicMock(default_dataset_id="ds-list-fail")
        return MagicMock(default_dataset_id="ds-list-ok")

    async def fake_items(dataset_id: str) -> list:
        if dataset_id == "ds-login":
            return [{"success": True, "cookies": [{"name": "auth", "value": "fresh"}]}]
        if dataset_id == "ds-list-fail":
            return [{"success": False, "message": "unauthenticated"}]
        return [{"email": "a@b.com", "firstName": "A", "lastName": "B"}]

    exporter._run_actor = fake_call  # type: ignore[assignment]
    exporter._fetch_dataset_items = fake_items  # type: ignore[assignment]

    output = tmp_path / "out.csv"
    await exporter.export_members("https://www.skool.com/group-1", "free", output)

    assert any(call.get("action") == "auth:login" for call in captured_calls)
    members_list_calls = [call for call in captured_calls if call.get("action") == "members:list"]
    assert len(members_list_calls) == 2
    assert members_list_calls[1].get("cookies") == [{"name": "auth", "value": "fresh"}]
