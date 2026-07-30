"""End-to-end test using synthetic CSVs that mirror the real Apify export format.

This test exercises the full parsing → normalization → state-computation pipeline
with realistic profile-picture URLs, ensuring the profile-pic hash identity key
correctly links the same person across the free and paid communities.
"""

from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.csv_parser import parse_csv
from src.exporters.dummy_exporter import DummySkoolExporter
from src.models import CommunityType
from src.normalizer import normalize_records
from src.sinks.noop_sink import NoOpSink
from src.sync_engine import SyncEngine


def _load_members_from_csv(path: Path, community: CommunityType, snapshot_date: str) -> list:
    raw_records = parse_csv(path)
    return normalize_records(
        raw_records=raw_records,
        community_type=community,
        community_name=community.value,
        community_slug=path.stem,
        source_file=str(path),
        snapshot_date=snapshot_date,
    )


def test_apify_like_samples_with_profile_pic_hash(tmp_path: Path) -> None:
    """Generated CSVs with profilePicUrl should produce clean, deterministic metrics."""
    # Import the generator here so the test file stays focused.
    from scripts.generate_sample_csvs import generate

    output_dir = tmp_path / "apify-like-test"
    generate(
        output_dir=output_dir,
        free_count=500,
        paid_count=100,
        overlap=30,
        seed=123,
    )

    free_members = _load_members_from_csv(output_dir / "free.csv", CommunityType.FREE, "2026-07-30")
    paid_members = _load_members_from_csv(output_dir / "paid.csv", CommunityType.PAID, "2026-07-30")

    assert len(free_members) == 500
    assert len(paid_members) == 100

    # All members should have a profile-pic URL, so the primary key is the hash.
    for member in free_members + paid_members:
        assert member.profile_pic_url, "Every generated member should have a profile-pic URL"

    settings = Settings(
        free_community_url="https://www.skool.com/free",
        paid_community_url="https://www.skool.com/paid",
    )
    engine = SyncEngine(
        settings,
        sink=NoOpSink(),
        exporter=DummySkoolExporter(),
        run_date="2026-07-30",
    )
    states = engine._compute_new_states({}, free_members, paid_members)
    metrics = engine._calculate_metrics({}, states, 0, 0.0)

    # 500 free + (100 - 30) paid-only + 30 overlap = 570 unique people
    assert len(states) == 570
    assert metrics.free_members_total == 500
    assert metrics.paid_members_total == 100
    assert metrics.converted_members == 30
    assert metrics.removed_free_members == 0
    assert metrics.removed_paid_members == 0

    # Every identity key should be a profile-pic hash, not a name or email fallback.
    for state in states.values():
        assert "|" not in state.key and not state.key.startswith("email:"), (
            f"Expected pic-hash key, got {state.key!r}"
        )
