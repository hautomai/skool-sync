"""Parse and lightly validate raw Skool CSV exports."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("skool_sync")

# Common column aliases seen in Skool exports.
_EMAIL_ALIASES = {"email", "email address", "email_address", "e-mail"}
_NAME_ALIASES = {"name", "full name", "full_name", "display name", "display_name", "fullname"}
_FIRST_NAME_ALIASES = {"first name", "first_name", "firstname"}
_LAST_NAME_ALIASES = {"last name", "last_name", "lastname"}
_JOINED_ALIASES = {"joined", "join date", "join_date", "joined at", "joined_at", "member since", "joinedat"}
_INVITED_ALIASES = {"invited by", "invited_by", "inviter", "invitedby"}
_ID_ALIASES = {"id", "member id", "member_id", "memberId", "skool id", "skool_id", "skool_member_id"}
_PROFILE_PIC_ALIASES = {"profilepicurl", "profile pic url", "profile_pic_url", "avatar", "picture", "profile_image"}


def _find_column(columns: list[str], aliases: set[str]) -> str | None:
    lowered = {c.lower().strip(): c for c in columns}
    for alias in aliases:
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    return None


def parse_csv(path: Path) -> list[dict[str, Any]]:
    """Parse a Skool member CSV into a list of raw record dicts.

    Args:
        path: Path to the downloaded CSV.

    Returns:
        List of raw records (column name -> value).
    """
    logger.info("Parsing CSV: %s", path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    records = df.to_dict(orient="records")
    logger.info("Parsed %d rows from %s", len(records), path)
    return records


def extract_standard_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw record to standard column names using heuristics."""
    columns = list(record.keys())

    def get(alias_set: set[str]) -> str:
        col = _find_column(columns, alias_set)
        return str(record.get(col or "", "")).strip() if col else ""

    return {
        "email": get(_EMAIL_ALIASES).lower().strip(),
        "full_name": get(_NAME_ALIASES),
        "first_name": get(_FIRST_NAME_ALIASES),
        "last_name": get(_LAST_NAME_ALIASES),
        "joined_at": get(_JOINED_ALIASES),
        "invited_by": get(_INVITED_ALIASES),
        "skool_member_id": get(_ID_ALIASES),
        "profile_pic_url": get(_PROFILE_PIC_ALIASES),
    }


def read_csv_to_dicts(path: Path) -> list[dict[str, Any]]:
    """CSV fallback using the standard library."""
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]
