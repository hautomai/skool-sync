import json
from pathlib import Path

import pytest

from src.cookie_manager import SkoolCookieManager


@pytest.fixture
def cookie_path(tmp_path: Path) -> Path:
    return tmp_path / "skool_cookies.json"


def test_load_missing_returns_none(cookie_path: Path) -> None:
    manager = SkoolCookieManager(cookie_path)
    assert manager.load() is None


def test_save_and_load_cookies(cookie_path: Path) -> None:
    manager = SkoolCookieManager(cookie_path)
    cookies = [{"name": "auth_token", "value": "abc123", "domain": "skool.com"}]
    manager.save(cookies)

    loaded = manager.load()
    assert loaded == cookies
    data = json.loads(cookie_path.read_text())
    assert "expires_at" in data
    assert data["cookies"] == cookies


def test_expired_cookies_returns_none(cookie_path: Path) -> None:
    manager = SkoolCookieManager(cookie_path)
    # Write expired data directly
    expired = {"expires_at": 0, "cookies": [{"name": "x", "value": "y"}]}
    cookie_path.write_text(json.dumps(expired))
    assert manager.load() is None


def test_clear_removes_file(cookie_path: Path) -> None:
    manager = SkoolCookieManager(cookie_path)
    manager.save([{"name": "x", "value": "y"}])
    assert cookie_path.exists()
    manager.clear()
    assert not cookie_path.exists()


def test_load_corrupt_file_returns_none(cookie_path: Path) -> None:
    cookie_path.write_text("not json")
    manager = SkoolCookieManager(cookie_path)
    assert manager.load() is None
