"""Shared utilities: logging, retry helpers, date handling."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as date_parser
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("skool_sync")


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure structured logging for the application."""
    logger = logging.getLogger("skool_sync")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def default_retry(attempts: int = 3):
    """Decorator factory for retryable operations."""
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(value: str) -> str:
    """Normalize a date/datetime string to YYYY-MM-DD HH:MM:SS.

    Preserves time when the source includes it (e.g. Skool/Apify exports).
    Falls back to midnight when only a date is provided so string sorting
    remains chronological and same-day conversions can be ordered.
    """
    if not value or not value.strip():
        return ""
    try:
        dt = date_parser.parse(value.strip())
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        logger.warning("Could not parse datetime value %r; keeping original", value)
        return value.strip()


def safe_filename(name: str) -> str:
    """Replace filesystem-unfriendly characters."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def _is_default_avatar(url: str) -> bool:
    """Return True when the URL points to a generic/default avatar."""
    lowered = url.lower()
    # Common default avatar path fragments used by Skool / generic CDNs.
    default_patterns = (
        "/default",
        "avatar.png",
        "avatar.jpg",
        "avatar.jpeg",
        "avatar.svg",
        "default_avatar",
        "placeholder",
        "no-image",
        "gravatar.com/avatar/0000000",
    )
    return any(pattern in lowered for pattern in default_patterns)


try:  # pragma: no cover - optional speedup
    from xxhash import xxh3_64_hexdigest as _xxh3_hexdigest

    _HAS_XXHASH = True
except ImportError:  # pragma: no cover - xxhash not installed
    _HAS_XXHASH = False


def _profile_pic_hash(profile_pic_url: str) -> str:
    """Return a short, stable hash of a normalized profile picture URL.

    - Strips query parameters and fragments (often contain dynamic tokens).
    - Strips trailing slashes.
    - Lower-cases the URL for consistency.
    - Prefers xxhash for speed, falls back to MD5 (no external dependency).
    """
    import hashlib
    from urllib.parse import urlparse

    url = (profile_pic_url or "").strip()
    if not url or _is_default_avatar(url):
        return ""

    parsed = urlparse(url)
    # Rebuild URL without query/fragment, preserving scheme and netloc.
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    if not parsed.scheme or not parsed.netloc:
        # Relative or malformed URL; hash the raw string without query params.
        path = parsed.path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        normalized = path or url

    if _HAS_XXHASH:
        return _xxh3_hexdigest(normalized.encode("utf-8"))[:16]
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]


def generate_key(
    profile_pic_url: str = "",
    email: str = "",
    first_name: str = "",
    last_name: str = "",
) -> str:
    """Stable identity key for a member.

    The Apify/Skool export includes a community-specific `memberId`, so it
    cannot be used to match the same person across free and paid communities.
    The profile picture URL, however, is global to the user. We hash the
    normalized URL to create a short, stable cross-community key.

    Fallback order:
        1. Hashed profile picture URL
        2. first_name|last_name
        3. email:<email>
    """
    pic_hash = _profile_pic_hash(profile_pic_url)
    if pic_hash:
        return f"pic:{pic_hash}"

    key = f"{first_name.strip().lower()}|{last_name.strip().lower()}"
    if key != "|":
        return key

    clean_email = email.strip().lower()
    if clean_email:
        return f"email:{clean_email}"

    return ""


