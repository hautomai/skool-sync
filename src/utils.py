"""Shared utilities: logging, retry helpers, date handling."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as date_parser
from tenacity import retry, stop_after_attempt, wait_exponential


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


def parse_iso_date(value: str) -> str:
    """Attempt to normalize a date string to ISO YYYY-MM-DD."""
    if not value or not value.strip():
        return ""
    try:
        return value.strip().split("T")[0]
    except Exception:
        pass
    try:
        dt = date_parser.parse(value)
        return dt.date().isoformat()
    except Exception:
        return value.strip()


def safe_filename(name: str) -> str:
    """Replace filesystem-unfriendly characters."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def generate_key(email: str = "", first_name: str = "", last_name: str = "") -> str:
    """Stable identity key for a member.

    Prefer email when available (it is unique and survives name changes).
    Fall back to a normalized first_name|last_name key when email is absent.
    """
    clean_email = email.strip().lower()
    if clean_email:
        return f"email:{clean_email}"
    key = f"{first_name.strip().lower()}|{last_name.strip().lower()}"
    if key == "|":
        return ""
    return key


