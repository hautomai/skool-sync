"""Tests for sync engine state computation."""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import Settings
from src.exporters.dummy_exporter import DummySkoolExporter
from src.models import CommunityType, Member, MemberState
from src.sinks.base import Sink
from src.sync_engine import SyncEngine


class DummySink(Sink):
    def fetch_existing(self):
        return {}, {}

    def write_members(self, members, existing_states, existing_ids):
        pass

    def write_daily_metrics(self, metrics):
        pass

    def write_sync_run(self, summary):
        pass


def _member(first_name: str, last_name: str, community: CommunityType, snapshot_date: str, email: str = "") -> Member:
    return Member(
        email=email,
        community_type=community,
        community_name=community.value,
        community_slug=community.value,
        source_file=f"{community.value}.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date=snapshot_date,
        first_name=first_name,
        last_name=last_name,
        full_name=f"{first_name} {last_name}".strip(),
    )


def test_compute_new_states_detects_conversion():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-02")

    states = engine._compute_new_states({}, [free_member], [])
    assert states["jane|doe"].current_status == "free_only"

    states = engine._compute_new_states(states, [], [paid_member])
    assert states["jane|doe"].conversion_detected_at == "2024-01-02 00:00:00"
    assert states["jane|doe"].current_status == "converted"


def test_members_without_email_are_matched_by_name():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    member_with_email = _member("John", "Doe", CommunityType.FREE, "2024-01-01", email="john@example.com")
    member_without_email = Member(
        email="",
        community_type=CommunityType.FREE,
        community_name="Free",
        community_slug="free",
        source_file="free.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-01",
        first_name="NoEmail",
        last_name="Person",
        full_name="NoEmail Person",
    )
    states = engine._compute_new_states({}, [member_with_email, member_without_email], [])
    assert "john|doe" in states
    assert "noemail|person" in states
    assert "" not in states


def test_email_fallback_links_records_when_names_missing():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    # Members with no names but the same email should be linked via the email fallback.
    free_member = Member(
        email="jane@example.com",
        community_type=CommunityType.FREE,
        community_name="Free",
        community_slug="free",
        source_file="free.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-01",
        first_name="",
        last_name="",
        full_name="",
    )
    paid_member = Member(
        email="jane@example.com",
        community_type=CommunityType.PAID,
        community_name="Paid",
        community_slug="paid",
        source_file="paid.csv",
        imported_at=datetime.now(timezone.utc),
        snapshot_date="2024-01-05",
        first_name="",
        last_name="",
        full_name="",
    )

    states = engine._compute_new_states({}, [free_member], [])
    assert states["email:jane@example.com"].email == "jane@example.com"
    assert states["email:jane@example.com"].current_status == "free_only"

    states = engine._compute_new_states(states, [], [paid_member])
    assert "email:jane@example.com" in states
    assert states["email:jane@example.com"].email == "jane@example.com"
    assert states["email:jane@example.com"].conversion_detected_at == "2024-01-05 00:00:00"
    assert states["email:jane@example.com"].current_status == "converted"


def test_duplicate_name_warning_is_logged(caplog):
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    member1 = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01")
    member2 = _member("Jane", "Doe", CommunityType.FREE, "2024-01-02")
    with caplog.at_level("WARNING"):
        engine._compute_new_states({}, [member1, member2], [])
    assert "Duplicate name key" in caplog.text


def test_conversion_detected_when_emails_differ_but_name_matches():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-05")

    states = engine._compute_new_states({}, [free_member], [paid_member])

    assert "jane|doe" in states
    assert states["jane|doe"].conversion_detected_at == "2024-01-05 00:00:00"
    assert states["jane|doe"].current_status == "converted"


def test_detected_conversions_are_incremental():
    """Conversions are counted once; repeated runs on the same converted member report 0."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-02")

    # First run: Jane converts.
    states = engine._compute_new_states({}, [free_member], [])
    states = engine._compute_new_states(states, [], [paid_member])
    metrics = engine._calculate_metrics({}, states, 0, 0.0)
    assert metrics.detected_conversions == 1
    assert metrics.new_paid_members == 1

    # Second run: same state, no new conversions.
    metrics = engine._calculate_metrics(states, states, 0, 0.0)
    assert metrics.detected_conversions == 0
    assert metrics.new_paid_members == 0
