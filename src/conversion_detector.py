"""Detect free-to-paid conversions and update member lifecycle state."""

from __future__ import annotations

import logging
from datetime import datetime

from .models import CommunityType, LifecycleStatus, Member, MemberState
from .utils import parse_iso_date

logger = logging.getLogger("skool_sync")


def build_initial_state(member: Member) -> MemberState:
    """Create a MemberState from the first Member record for a person."""
    return MemberState(
        email=member.email,
        first_name=member.first_name,
        last_name=member.last_name,
        full_name=member.full_name,
        membership_answers=member.membership_answers,
    )


def _is_active(member_state: MemberState, community: CommunityType) -> bool:
    if community == CommunityType.FREE:
        return member_state.free_status == "active"
    return member_state.paid_status == "active"


def apply_membership(
    state: MemberState,
    member: Member,
    community: CommunityType,
    today: str,
) -> MemberState:
    """Apply a single normalized member record to a MemberState."""
    status_field = "free_status" if community == CommunityType.FREE else "paid_status"
    joined_field = "free_joined_at" if community == CommunityType.FREE else "paid_joined_at"
    left_field = "free_left_at" if community == CommunityType.FREE else "paid_left_at"
    first_seen_field = "first_seen_free_at" if community == CommunityType.FREE else "first_seen_paid_at"
    source_file_field = "free_source_file" if community == CommunityType.FREE else "paid_source_file"

    # Update to active
    setattr(state, status_field, "active")
    setattr(state, joined_field, parse_iso_date(member.joined_at) if member.joined_at else today)
    setattr(state, left_field, "")
    setattr(state, source_file_field, member.source_file)

    # Track first seen using the member's snapshot date so backfills are accurate
    seen_at = member.snapshot_date or today
    existing_first = getattr(state, first_seen_field)
    if not existing_first or seen_at < existing_first:
        setattr(state, first_seen_field, seen_at)

    # Detect free-to-paid conversion at the moment paid membership is applied
    if community == CommunityType.PAID and state.first_seen_free_at:
        if not state.conversion_detected_at or seen_at < state.conversion_detected_at:
            state.conversion_detected_at = seen_at

    # Update email if the incoming record has one and the state does not.
    if member.email and not state.email:
        state.email = member.email

    # Update names if blank
    if member.first_name and not state.first_name:
        state.first_name = member.first_name
    if member.last_name and not state.last_name:
        state.last_name = member.last_name
    if member.full_name and not state.full_name:
        state.full_name = member.full_name

    if member.membership_answers:
        state.membership_answers.update(member.membership_answers)

    return state


def flag_removed(
    state: MemberState,
    community: CommunityType,
    today: str,
) -> MemberState:
    """Mark a member as removed from a community."""
    if community == CommunityType.FREE:
        if state.free_status == "active":
            state.free_status = "removed"
            state.free_left_at = today
    else:
        if state.paid_status == "active":
            state.paid_status = "removed"
            state.paid_left_at = today
    return state


def detect_conversions(states: dict[str, MemberState]) -> dict[str, MemberState]:
    """After applying all community data, set the current lifecycle status."""
    for key, state in states.items():
        was_free = bool(state.first_seen_free_at)
        is_paid = state.paid_status == "active"
        is_free_active = state.free_status == "active"

        # Conversion is the most interesting business signal, so it takes precedence
        # over "both" when a member was ever seen in the free community.
        if was_free and is_paid:
            state.current_status = LifecycleStatus.CONVERTED.value
        elif is_free_active and is_paid:
            state.current_status = LifecycleStatus.BOTH.value
        elif is_paid:
            state.current_status = LifecycleStatus.PAID_ONLY.value
        elif is_free_active:
            state.current_status = LifecycleStatus.FREE_ONLY.value
        else:
            state.current_status = LifecycleStatus.FREE_ONLY.value
    return states
