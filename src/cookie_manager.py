"""Skool cookie cache manager.

The Apify actor ``cristiantala/skool-all-in-one-api`` supports an ``auth:login``
action that returns a Playwright cookie array. This module caches those cookies
locally so subsequent ``members:list`` calls can reuse them without performing a
full browser login every run.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("skool_sync")


class SkoolCookieManager:
    """Load, store, and refresh Skool session cookies for the Apify actor."""

    def __init__(self, path: Path, refresh_hours: int = 24) -> None:
        self.path = path
        self.refresh_hours = refresh_hours

    def load(self) -> list[dict[str, Any]] | None:
        """Return cached cookies if they exist and are still fresh."""
        if not self.path.exists():
            return None

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read cookie cache at %s: %s", self.path, exc)
            return None

        expires_at = data.get("expires_at")
        if not expires_at or time.time() > expires_at:
            logger.info("Cached Skool cookies have expired.")
            return None

        cookies = data.get("cookies")
        if not cookies:
            logger.warning("Cached cookie file has no cookies.")
            return None

        logger.debug("Loaded cached Skool cookies (expires at %s)", time.ctime(expires_at))
        return cookies

    def save(self, cookies: list[dict[str, Any]], expires_at: int | None = None) -> None:
        """Persist cookies with an expiry timestamp.

        If ``expires_at`` is not provided, the configured refresh window is used.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if expires_at is None:
            expires_at = int(time.time() + self.refresh_hours * 3600)
        payload = {"expires_at": expires_at, "cookies": cookies}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Restrict permissions: only the owner can read/write.
        self.path.chmod(0o600)
        logger.info("Cached Skool cookies until %s", time.ctime(expires_at))

    def clear(self) -> None:
        """Delete the cached cookie file."""
        if self.path.exists():
            self.path.unlink()
            logger.info("Cleared cached Skool cookies.")
