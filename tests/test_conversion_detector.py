"""Tests for free-to-paid conversion detection."""

from __future__ import annotations

from datetime import datetime, timezone

from src.conversion_detector import apply_membership, build_initial_state, detect_conversions
from src.models import CommunityType, Member


def _member(community_type: CommunityType, snapshot_date: str, email: str = "") -> Member:
    return Member(
        email=email,
        community_type=community_type,
        community_name=community_type.value,
        community_slug=community_type.value,
        source_file=f"{community_type.value}.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date=snapshot_date,
    )


def test_detects_conversion_on_day_paid_appears():
    state = build_initial_state(_member(CommunityType.FREE, "2024-01-01"))
    state = apply_membership(state, _member(CommunityType.FREE, "2024-01-01"), CommunityType.FREE, "2024-01-01 10:00:00")
    state = apply_membership(state, _member(CommunityType.PAID, "2024-01-05"), CommunityType.PAID, "2024-01-05 14:30:00")
    assert state.first_seen_free_at == "2024-01-01 00:00:00"
    assert state.first_seen_paid_at == "2024-01-05 00:00:00"
    assert state.conversion_detected_at == "2024-01-05 00:00:00"


def test_conversion_date_preserved_across_runs():
    state = build_initial_state(_member(CommunityType.FREE, "2024-01-01"))
    state = apply_membership(state, _member(CommunityType.FREE, "2024-01-01"), CommunityType.FREE, "2024-01-01 10:00:00")
    state = apply_membership(state, _member(CommunityType.PAID, "2024-01-05"), CommunityType.PAID, "2024-01-05 14:30:00")
    # Later run with a newer paid snapshot should not overwrite conversion_detected_at
    state = apply_membership(state, _member(CommunityType.PAID, "2024-01-10"), CommunityType.PAID, "2024-01-10 09:00:00")
    assert state.conversion_detected_at == "2024-01-05 00:00:00"


def test_both_status_when_active_in_both():
    state = build_initial_state(_member(CommunityType.FREE, "2024-01-01"))
    state = apply_membership(state, _member(CommunityType.FREE, "2024-01-01"), CommunityType.FREE, "2024-01-01 10:00:00")
    state = apply_membership(state, _member(CommunityType.PAID, "2024-01-02"), CommunityType.PAID, "2024-01-02 14:30:00")
    states = detect_conversions({"jane|doe": state})
    assert states["jane|doe"].current_status == "converted"


def test_email_carried_into_state():
    member = _member(CommunityType.FREE, "2024-01-01", email="jane@example.com")
    state = build_initial_state(member)
    assert state.email == "jane@example.com"
    state = apply_membership(state, _member(CommunityType.PAID, "2024-01-02"), CommunityType.PAID, "2024-01-02 14:30:00")
    assert state.email == "jane@example.com"


def test_same_day_conversion_preserves_order():
    """If a member joins free and paid on the same day, timestamps order the events."""
    free = Member(
        email="jane@example.com",
        community_type=CommunityType.FREE,
        community_name="Free",
        community_slug="free",
        source_file="free.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-01",
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        joined_at="2024-01-01 09:15:00",
    )
    paid = Member(
        email="jane@example.com",
        community_type=CommunityType.PAID,
        community_name="Paid",
        community_slug="paid",
        source_file="paid.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-01",
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        joined_at="2024-01-01 14:30:00",
    )
    state = build_initial_state(free)
    state = apply_membership(state, free, CommunityType.FREE, "2024-01-01 18:00:00")
    assert state.first_seen_free_at == "2024-01-01 09:15:00"
    state = apply_membership(state, paid, CommunityType.PAID, "2024-01-01 18:00:00")
    assert state.first_seen_paid_at == "2024-01-01 14:30:00"
    assert state.conversion_detected_at == "2024-01-01 14:30:00"


def test_date_only_joined_at_is_normalized_to_midnight():
    """A CSV date without time is normalized to midnight for consistent formatting."""
    member = Member(
        email="john@example.com",
        community_type=CommunityType.FREE,
        community_name="Free",
        community_slug="free",
        source_file="free.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-01",
        first_name="John",
        last_name="Doe",
        full_name="John Doe",
        joined_at="2024-01-01",
    )
    state = build_initial_state(member)
    state = apply_membership(state, member, CommunityType.FREE, "2024-01-01 10:00:00")
    assert state.free_joined_at == "2024-01-01 00:00:00"
    assert state.first_seen_free_at == "2024-01-01 00:00:00"


def test_removed_member_status():
    state = build_initial_state(_member(CommunityType.FREE, "2024-01-01"))
    from src.conversion_detector import flag_removed
    state = apply_membership(state, _member(CommunityType.FREE, "2024-01-01"), CommunityType.FREE, "2024-01-01 10:00:00")
    state = flag_removed(state, CommunityType.FREE, "2024-01-03 18:00:00")
    assert state.free_status == "removed"
    assert state.free_left_at == "2024-01-03 18:00:00"
