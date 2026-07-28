"""Tests for record normalization."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import CommunityType
from src.normalizer import normalize_record


@pytest.fixture
def raw_record():
    return {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Joined": "2024-01-15",
        "Invited By": "John",
        "custom question": "answer",
    }


def test_normalize_builds_full_name(raw_record):
    raw_record.pop("Full Name", None)
    member = normalize_record(
        raw=raw_record,
        community_type=CommunityType.FREE,
        community_name="Free Community",
        community_slug="free-community",
        source_file="/tmp/free.csv",
        snapshot_date="2024-01-01",
        imported_at=datetime.now(timezone.utc),
    )
    assert member.full_name == "Jane Doe"


def test_normalize_keeps_member_with_no_email():
    raw = {
        "First Name": "NoEmail",
        "Last Name": "Person",
        "Joined": "2024-02-01",
    }
    member = normalize_record(
        raw=raw,
        community_type=CommunityType.FREE,
        community_name="Free Community",
        community_slug="free-community",
        source_file="/tmp/free.csv",
        snapshot_date="2024-01-01",
        imported_at=datetime.now(timezone.utc),
    )
    assert member.email == ""
    assert member.first_name == "NoEmail"
    assert member.last_name == "Person"
    assert member.key == "noemail|person"


def test_normalize_extracts_and_excludes_email():
    raw = {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Email": "Jane@Example.COM",
        "Joined": "2024-02-01",
    }
    member = normalize_record(
        raw=raw,
        community_type=CommunityType.FREE,
        community_name="Free Community",
        community_slug="free-community",
        source_file="/tmp/free.csv",
        snapshot_date="2024-01-01",
        imported_at=datetime.now(timezone.utc),
    )
    assert "Email" not in member.membership_answers
    assert member.email == "jane@example.com"
    assert member.match_key == "email:jane@example.com"


def test_normalize_splits_full_name_when_first_last_missing():
    raw = {
        "Name": "Alice Smith",
        "Joined": "2024-02-01",
    }
    member = normalize_record(
        raw=raw,
        community_type=CommunityType.FREE,
        community_name="Free Community",
        community_slug="free-community",
        source_file="/tmp/free.csv",
        snapshot_date="2024-01-01",
        imported_at=datetime.now(timezone.utc),
    )
    assert member.first_name == "Alice"
    assert member.last_name == "Smith"
