"""Generate human-readable and JSON reports after a sync run."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import SyncSummary
from .utils import ensure_dir

logger = logging.getLogger("skool_sync")


def human_summary(summary: SyncSummary) -> str:
    lines = [
        "=" * 50,
        "Skool Sync Summary",
        "=" * 50,
        f"Run ID:        {summary.run_id}",
        f"Started:       {summary.started_at.isoformat()}",
        f"Finished:      {summary.finished_at.isoformat()}",
        f"Runtime:       {summary.runtime_seconds:.2f}s",
        f"Dry run:       {summary.dry_run}",
        "",
        "Membership",
        f"  Free members:         {summary.free_members_total}",
        f"  Paid members:         {summary.paid_members_total}",
        f"  Converted members:    {summary.converted_members}",
        f"  Removed free members: {summary.removed_free_members}",
        f"  Removed paid members: {summary.removed_paid_members}",
        f"  Failed records:       {summary.failed_records}",
    ]
    if summary.notes:
        lines.extend(["", "Notes:", *summary.notes])
    return "\n".join(lines)


def write_report(summary: SyncSummary, reports_dir: Path) -> Path:
    ensure_dir(reports_dir)
    path = reports_dir / f"sync_summary_{summary.run_id}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary.__dict__, f, indent=2, default=str)
    logger.info("JSON summary written to %s", path)

    human_path = reports_dir / f"sync_summary_{summary.run_id}.txt"
    with human_path.open("w", encoding="utf-8") as f:
        f.write(human_summary(summary))
    logger.info("Readable summary written to %s", human_path)
    return path
