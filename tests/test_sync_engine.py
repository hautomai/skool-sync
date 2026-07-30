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


def _member(
    first_name: str,
    last_name: str,
    community: CommunityType,
    snapshot_date: str,
    email: str = "",
    skool_member_id: str = "",
    profile_pic_url: str = "",
) -> Member:
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
        skool_member_id=skool_member_id,
        profile_pic_url=profile_pic_url,
    )


def test_compute_new_states_detects_conversion():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-123")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-02", skool_member_id="paid-456")

    states = engine._compute_new_states({}, [free_member], [])
    assert states["jane|doe"].current_status == "free_only"

    states = engine._compute_new_states(states, [], [paid_member])
    assert states["jane|doe"].conversion_detected_at == "2024-01-02 00:00:00"
    assert states["jane|doe"].current_status == "converted"
    assert states["jane|doe"].skool_member_id == "paid-456"


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
    # When no member id is present, the fallback name key must be unique.
    member1 = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01")
    member2 = _member("Jane", "Doe", CommunityType.FREE, "2024-01-02")
    with caplog.at_level("WARNING"):
        engine._compute_new_states({}, [member1, member2], [])
    assert "Duplicate" in caplog.text and "key" in caplog.text


def test_conversion_detected_when_emails_differ_but_name_matches():
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-789")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-05", skool_member_id="paid-789")

    states = engine._compute_new_states({}, [free_member], [paid_member])

    assert "jane|doe" in states
    assert states["jane|doe"].conversion_detected_at == "2024-01-05 00:00:00"
    assert states["jane|doe"].current_status == "converted"


def test_metrics_reflect_membership_snapshot():
    """Daily metrics reflect the current membership snapshot, including conversions."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-metrics")
    paid_member = _member("Jane", "Doe", CommunityType.PAID, "2024-01-02", skool_member_id="paid-metrics")

    # Jane is active in both communities: she counts as converted.
    states = engine._compute_new_states({}, [free_member], [paid_member])
    metrics = engine._calculate_metrics({}, states, 0, 0.0)
    assert metrics.free_members_total == 1
    assert metrics.paid_members_total == 1
    assert metrics.converted_members == 1
    assert metrics.removed_free_members == 0
    assert metrics.removed_paid_members == 0

    # Recomputing the same state keeps the same snapshot counts.
    metrics = engine._calculate_metrics(states, states, 0, 0.0)
    assert metrics.converted_members == 1
    assert metrics.removed_free_members == 0
    assert metrics.removed_paid_members == 0


def test_removed_counts_are_incremental():
    """Removed counts only include members who were active and are now removed."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())
    member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-remove")

    # Day 1: Jane is active in free.
    states_day1 = engine._compute_new_states({}, [member], [])
    metrics = engine._calculate_metrics({}, states_day1, 0, 0.0)
    assert metrics.free_members_total == 1
    assert metrics.removed_free_members == 0

    # Day 2: Jane is no longer in the free CSV.
    states_day2 = engine._compute_new_states(states_day1, [], [])
    metrics = engine._calculate_metrics(states_day1, states_day2, 0, 0.0)
    assert metrics.free_members_total == 0
    assert metrics.removed_free_members == 1


def test_legacy_name_key_migrates_to_member_id():
    """A state keyed only by name is migrated to the member id key and not duplicated."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    # Day 1: only name-based key exists (legacy state).
    legacy_state = MemberState(
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        free_status="active",
    )
    legacy_states = {"jane|doe": legacy_state}

    # Day 2: the same person arrives with a (community-specific) member id.
    new_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-02", skool_member_id="free-migrate")
    states = engine._compute_new_states(legacy_states, [new_member], [])

    # Should have exactly one active free member, keyed by name.
    assert len(states) == 1
    assert "jane|doe" in states
    assert states["jane|doe"].free_status == "active"
    assert states["jane|doe"].first_name == "Jane"
    assert states["jane|doe"].skool_member_id == "free-migrate"


def test_member_id_state_matched_by_name_does_not_create_ghost():
    """If an existing state has a member id but a new record only matches by name, no ghost is created."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    # Day 1: state keyed by name.
    state_day1 = MemberState(
        skool_member_id="paid-ghost",
        first_name="Jane",
        last_name="Doe",
        full_name="Jane Doe",
        free_status="active",
    )

    # Day 2: incoming paid record has a different community-specific member id, same name.
    new_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-02", skool_member_id="free-ghost")
    states = engine._compute_new_states({"jane|doe": state_day1}, [new_member], [])

    # Exactly one active state, matched by name; paid member id is preserved.
    assert len(states) == 1
    assert "jane|doe" in states
    assert states["jane|doe"].free_status == "active"
    assert states["jane|doe"].skool_member_id == "free-ghost"


def test_profile_pic_hash_links_members_across_communities():
    """Members with the same profile pic URL but different names/ids are treated as one person."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    url = "https://cdn.skool.com/users/abc123/profile.png?v=1"
    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-1", profile_pic_url=url)
    # Same person in paid community with a different community-specific id and a typo in name.
    paid_member = _member("Jane", "Dow", CommunityType.PAID, "2024-01-05", skool_member_id="paid-1", profile_pic_url=url)

    states = engine._compute_new_states({}, [free_member], [paid_member])

    # They should be merged under the hashed profile-pic key.
    pic_key = free_member.key
    assert pic_key.startswith("pic:")
    assert pic_key == paid_member.key
    assert len(states) == 1
    assert states[pic_key].current_status == "converted"
    assert states[pic_key].skool_member_id == "free-1"


def test_profile_pic_hash_falls_back_to_name_when_url_missing():
    """Members without a profile pic URL still match by name."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    free_member = _member("John", "Smith", CommunityType.FREE, "2024-01-01", skool_member_id="free-1")
    paid_member = _member("John", "Smith", CommunityType.PAID, "2024-01-05", skool_member_id="paid-1")

    states = engine._compute_new_states({}, [free_member], [paid_member])

    assert "john|smith" in states
    assert states["john|smith"].current_status == "converted"


def test_default_avatar_falls_back_to_name():
    """A default/placeholder avatar URL should not be used as identity key."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    url = "https://cdn.skool.com/default_avatar.png"
    free_member = _member("John", "Smith", CommunityType.FREE, "2024-01-01", skool_member_id="free-1", profile_pic_url=url)
    paid_member = _member("John", "Smith", CommunityType.PAID, "2024-01-05", skool_member_id="paid-1", profile_pic_url=url)

    states = engine._compute_new_states({}, [free_member], [paid_member])

    # Should fall back to name key because the avatar is a default placeholder.
    assert "john|smith" in states
    assert states["john|smith"].current_status == "converted"


def test_profile_pic_change_warns_and_migrates_key(caplog):
    """When a member's profile pic URL changes, the engine falls back to name match and migrates the key."""
    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(settings, sink=DummySink(), exporter=DummySkoolExporter())

    old_url = "https://cdn.skool.com/users/abc123/profile.png"
    new_url = "https://cdn.skool.com/users/abc123/new_profile.png"

    free_member = _member("Jane", "Doe", CommunityType.FREE, "2024-01-01", skool_member_id="free-1", profile_pic_url=old_url)
    states_day1 = engine._compute_new_states({}, [free_member], [])

    # Day 2: same person, different profile pic URL.
    free_member_day2 = _member("Jane", "Doe", CommunityType.FREE, "2024-01-02", skool_member_id="free-1", profile_pic_url=new_url)

    with caplog.at_level("WARNING"):
        states_day2 = engine._compute_new_states(states_day1, [free_member_day2], [])

    # Should still be one active member, migrated to the new pic-hash key.
    new_key = free_member_day2.key
    assert new_key.startswith("pic:")
    assert len(states_day2) == 1
    assert states_day2[new_key].free_status == "active"
    assert states_day2[new_key].profile_pic_url == new_url
    assert "Profile picture URL changed" in caplog.text
