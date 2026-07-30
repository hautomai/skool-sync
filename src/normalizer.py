"""Normalize raw Skool CSV rows into Member dataclasses."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .csv_parser import extract_standard_fields
from .models import CommunityType, Member

logger = logging.getLogger("skool_sync")


def normalize_record(
    raw: dict[str, Any],
    community_type: CommunityType,
    community_name: str,
    community_slug: str,
    source_file: str,
    snapshot_date: str,
    imported_at: datetime,
) -> Member | None:
    """Convert a raw CSV row into a normalized Member object."""
    fields = extract_standard_fields(raw)

    # Build full name from first/last if missing
    first = fields["first_name"] or ""
    last = fields["last_name"] or ""
    full = fields["full_name"] or ""

    # If first/last are missing but full name is available, attempt a simple split.
    if not first and not last and full:
        parts = full.strip().split(maxsplit=1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""

    if full:
        pass
    elif first or last:
        full = f"{first} {last}".strip()

    if not first and not last:
        logger.warning("Row without a recognizable name skipped during normalization: %s", raw)
        return None

    membership_answers = {
        k: v for k, v in raw.items()
        if k and k.lower() not in {"email", "name", "full name", "first name", "last name", "joined", "invited by"}
    }

    return Member(
        email=fields["email"],
        community_type=community_type,
        community_name=community_name,
        community_slug=community_slug,
        source_file=source_file,
        imported_at=imported_at,
        snapshot_date=snapshot_date,
        first_name=first,
        last_name=last,
        full_name=full,
        joined_at=fields["joined_at"],
        invited_by=fields["invited_by"],
        membership_answers=membership_answers,
        skool_member_id=fields["skool_member_id"],
        profile_pic_url=fields["profile_pic_url"],
        raw_record=raw,
    )


def normalize_records(
    raw_records: list[dict[str, Any]],
    community_type: CommunityType,
    community_name: str,
    community_slug: str,
    source_file: str,
    snapshot_date: str,
) -> list[Member]:
    """Normalize many raw records."""
    imported_at = datetime.utcnow()
    members: list[Member] = []
    for raw in raw_records:
        try:
            member = normalize_record(
                raw=raw,
                community_type=community_type,
                community_name=community_name,
                community_slug=community_slug,
                source_file=source_file,
                snapshot_date=snapshot_date,
                imported_at=imported_at,
            )
            if member is not None:
                members.append(member)
        except Exception as exc:
            logger.exception("Failed to normalize row %s: %s", raw, exc)
    return members
