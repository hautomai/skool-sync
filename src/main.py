"""Main entry point for the daily Skool → Google Sheets sync.

Usage:
    python -m src.main
    python -m src.main --dry-run
    python -m src.main --backfill data/raw/2025-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path

from .config import Settings, get_settings
from .csv_parser import parse_csv
from .models import CommunityType, MemberState, SyncSummary
from .normalizer import normalize_records
from .reporter import human_summary, write_report
from .exporters.dummy_exporter import DummySkoolExporter
from .sinks.google_sheets_sink import GoogleSheetsSink
from .sync_engine import SyncEngine
from .utils import setup_logging, utc_now

logger = logging.getLogger("skool_sync")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Skool to Airtable daily sync")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without writing to the sink; log intended actions.",
    )
    parser.add_argument(
        "--backfill",
        type=str,
        default=None,
        help="Path to a folder containing free.csv and paid.csv snapshots to backfill.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser.parse_args()


def _load_snapshot(path: Path, community_type: CommunityType, snapshot_date: str) -> list:
    raw_records = parse_csv(path)
    slug = path.stem
    return normalize_records(
        raw_records=raw_records,
        community_type=community_type,
        community_name=slug.replace("-", " ").title(),
        community_slug=slug,
        source_file=str(path),
        snapshot_date=snapshot_date,
    )


def _backfill_from_dir(settings: Settings, backfill_dir: str) -> None:
    """Backfill Google Sheets from a directory of pre-exported CSVs.

    Expects files like:
        backfill_dir/free.csv
        backfill_dir/paid.csv
    """
    base = Path(backfill_dir)
    if not base.exists():
        raise FileNotFoundError(f"Backfill directory not found: {base}")

    snapshot_date = base.name if re.match(r"^\d{4}-\d{2}-\d{2}$", base.name) else "backfill"
    free_path = base / "free.csv"
    paid_path = base / "paid.csv"

    free_members = _load_snapshot(free_path, CommunityType.FREE, snapshot_date) if free_path.exists() else []
    paid_members = _load_snapshot(paid_path, CommunityType.PAID, snapshot_date) if paid_path.exists() else []

    logger.info("Backfilling from %s with %d free and %d paid members", base, len(free_members), len(paid_members))

    sink = GoogleSheetsSink(settings) if not settings.dry_run else None

    existing_states: dict[str, MemberState] = {}
    existing_ids: dict[str, str] = {}
    if sink is not None:
        existing_states, existing_ids = sink.fetch_existing()

    started_at = utc_now()
    engine = SyncEngine(
        settings,
        exporter=DummySkoolExporter(),
        sink=sink,
        run_date=snapshot_date,
    )
    previous_states = engine._read_local_state()
    states = engine._compute_new_states(previous_states, free_members, paid_members)

    if sink is not None:
        sink.write_members(list(states.values()), existing_states, existing_ids)
        engine._write_local_state(states)

    finished_at = utc_now()
    runtime = (finished_at - started_at).total_seconds()
    metrics = engine._calculate_metrics(previous_states, states, failed_records=0, runtime_seconds=runtime)

    if sink is not None:
        sink.write_daily_metrics(metrics)

    summary = SyncSummary(
        run_id=f"backfill-{snapshot_date}-{started_at.strftime('%H%M%S')}",
        started_at=started_at,
        finished_at=finished_at,
        free_members_total=metrics.free_members_total,
        paid_members_total=metrics.paid_members_total,
        converted_members=metrics.converted_members,
        removed_free_members=metrics.removed_free_members,
        removed_paid_members=metrics.removed_paid_members,
        failed_records=metrics.failed_records,
        runtime_seconds=metrics.runtime_seconds,
        communities=[settings.free_community_url, settings.paid_community_url],
        notes=[f"Backfill from {base}"],
        dry_run=settings.dry_run,
    )

    if sink is not None:
        sink.write_sync_run(summary.__dict__)

    logger.info("Backfill complete: %d member records written", len(states))


async def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True

    if args.backfill:
        _backfill_from_dir(settings, args.backfill)
        return

    engine = SyncEngine(settings)
    summary = await engine.run()
    report_path = write_report(summary, Path(settings.reports_dir))
    print(human_summary(summary))
    logger.info("Sync complete. Report: %s", report_path)


if __name__ == "__main__":
    asyncio.run(main())
