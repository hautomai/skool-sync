"""Sync engine: orchestrates the full daily sync pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Settings
from .conversion_detector import (
    apply_membership,
    build_initial_state,
    detect_conversions,
    flag_removed,
)
from .csv_parser import parse_csv
from .exporters.apify_exporter import ApifySkoolExporter
from .exporters.base import SkoolExporter
from .models import CommunityType, DailyMetrics, Member, MemberState, SyncSummary
from .normalizer import normalize_records
from .sinks.base import Sink
from .sinks.google_sheets_sink import GoogleSheetsSink
from .sinks.noop_sink import NoOpSink
from .utils import ensure_dir, safe_filename, utc_now

logger = logging.getLogger("skool_sync")


class SyncEngine:
    def __init__(
        self,
        settings: Settings,
        exporter: SkoolExporter | None = None,
        sink: Sink | None = None,
        run_date: str | None = None,
    ):
        self.settings = settings
        self.exporter = exporter if exporter is not None else self._build_exporter()
        self.sink = sink if sink is not None else self._build_sink()
        self.today = run_date if run_date else utc_now().date().isoformat()
        self.snapshot_dir = ensure_dir(Path(self.settings.download_dir) / self.today)

    def _build_exporter(self) -> SkoolExporter:
        return ApifySkoolExporter(self.settings)

    def _build_sink(self) -> Sink:
        if self.settings.dry_run:
            return NoOpSink()
        return GoogleSheetsSink(self.settings)

    async def _download_community_csv(
        self,
        community_type: CommunityType,
        community_url: str,
    ) -> Path:
        community_slug = Path(community_url).name or community_type.value
        output_path = self.snapshot_dir / f"{community_type.value}_{safe_filename(community_slug)}.csv"
        if self.settings.dry_run:
            logger.info("[DRY RUN] Skipping live export for %s; will use %s if it exists", community_url, output_path)
            return output_path

        output_path = await self.exporter.export_members(
            community_url=community_url,
            community_type=community_type.value,
            output_path=output_path,
        )
        return output_path

    def _load_members(self, path: Path, community_type: CommunityType) -> list[Member]:
        if not path.exists():
            if self.settings.dry_run:
                logger.info("[DRY RUN] Snapshot not found, treating as empty: %s", path)
                return []
            raise FileNotFoundError(f"CSV not found: {path}")
        raw_records = parse_csv(path)
        community_slug = path.stem
        community_name = community_slug.replace("-", " ").title()
        return normalize_records(
            raw_records=raw_records,
            community_type=community_type,
            community_name=community_name,
            community_slug=community_slug,
            source_file=str(path),
            snapshot_date=self.today,
        )

    def _warn_duplicate_keys(self, members: list[Member], community: CommunityType) -> None:
        """Log a warning when two active members share the same first+last name."""
        from collections import Counter

        counts: dict[str, int] = Counter(m.key for m in members if m.key)
        for key, count in counts.items():
            if count > 1:
                first, last = key.split("|", 1)
                logger.warning(
                    "Duplicate name key in %s community: %s %s appears %d times. "
                    "These rows will be merged into a single member record.",
                    community.value,
                    first,
                    last,
                    count,
                )

    def _compute_new_states(
        self,
        existing: dict[str, MemberState],
        free_members: list[Member],
        paid_members: list[Member],
    ) -> dict[str, MemberState]:
        # Warn about duplicate names within the same community before indexing.
        self._warn_duplicate_keys(free_members, CommunityType.FREE)
        self._warn_duplicate_keys(paid_members, CommunityType.PAID)

        # Daily merge uses name key because one side may not have an email.
        free_by_key: dict[str, Member] = {m.key: m for m in free_members if m.key}
        paid_by_key: dict[str, Member] = {m.key: m for m in paid_members if m.key}
        all_name_keys = set(free_by_key.keys()) | set(paid_by_key.keys())

        # Historical lookup by match key (email when available, otherwise name).
        existing_by_match_key: dict[str, MemberState] = {}
        for state in existing.values():
            key = state.match_key
            if key in existing_by_match_key:
                logger.warning(
                    "Duplicate match key in existing data: %s. "
                    "Two different member records share the same email/name.",
                    key,
                )
            else:
                existing_by_match_key[key] = state

        # Track existing states already claimed by today's records so we don't
        # incorrectly mark them as removed when the matching key changed.
        claimed_existing_name_keys: set[str] = set()

        # Track today's final states keyed by name (for reporting/removals).
        states: dict[str, MemberState] = {}

        for name_key in all_name_keys:
            free_member = free_by_key.get(name_key)
            paid_member = paid_by_key.get(name_key)
            representative = free_member or paid_member

            # Try to find an existing state. Prefer email-based match, fall back to name.
            state: MemberState | None = None
            if representative and representative.email:
                state = existing_by_match_key.get(representative.match_key)
            if state is None:
                state = existing.get(name_key)

            if state is None:
                state = MemberState(
                    email=representative.email if representative else "",
                    first_name=representative.first_name if representative else "",
                    last_name=representative.last_name if representative else "",
                    full_name=representative.full_name if representative else "",
                )
            else:
                # Record that this historical state has been claimed by a
                # current record (possibly under a different name key).
                for existing_name_key, existing_state in existing.items():
                    if existing_state is state:
                        claimed_existing_name_keys.add(existing_name_key)
                        break

                # If the record was found by email but the name changed,
                # update the state so its key matches the current row.
                if representative:
                    if representative.first_name:
                        state.first_name = representative.first_name
                    if representative.last_name:
                        state.last_name = representative.last_name
                    if representative.full_name:
                        state.full_name = representative.full_name

            if free_member:
                state = apply_membership(state, free_member, CommunityType.FREE, self.today)
            else:
                state = flag_removed(state, CommunityType.FREE, self.today)

            if paid_member:
                state = apply_membership(state, paid_member, CommunityType.PAID, self.today)
            else:
                state = flag_removed(state, CommunityType.PAID, self.today)

            state.last_synced_at = utc_now().isoformat()
            states[name_key] = state

        # Members no longer in either community should be flagged removed
        for name_key, state in existing.items():
            if name_key in claimed_existing_name_keys:
                continue
            if name_key not in all_name_keys:
                state = flag_removed(state, CommunityType.FREE, self.today)
                state = flag_removed(state, CommunityType.PAID, self.today)
                state.last_synced_at = utc_now().isoformat()
                states[name_key] = state

        return detect_conversions(states)

    def _calculate_metrics(
        self,
        existing: dict[str, MemberState],
        new_states: dict[str, MemberState],
        failed_records: int,
        runtime_seconds: float,
    ) -> DailyMetrics:
        new_free = 0
        new_paid = 0
        conversions = 0
        removed_free = 0
        removed_paid = 0

        for key, state in new_states.items():
            old = existing.get(key)
            if state.free_status == "active" and (not old or old.free_status != "active"):
                new_free += 1
            if state.paid_status == "active" and (not old or old.paid_status != "active"):
                new_paid += 1
            if state.conversion_detected_at == self.today:
                conversions += 1
            if old and old.free_status == "active" and state.free_status == "removed":
                removed_free += 1
            if old and old.paid_status == "active" and state.paid_status == "removed":
                removed_paid += 1

        return DailyMetrics(
            date=self.today,
            free_members_total=sum(1 for s in new_states.values() if s.free_status == "active"),
            paid_members_total=sum(1 for s in new_states.values() if s.paid_status == "active"),
            new_free_members=new_free,
            new_paid_members=new_paid,
            detected_conversions=conversions,
            removed_free_members=removed_free,
            removed_paid_members=removed_paid,
            failed_records=failed_records,
            runtime_seconds=runtime_seconds,
            snapshot_date=self.today,
        )

    async def run(self) -> SyncSummary:
        started_at = utc_now()
        notes: list[str] = []
        failed_records = 0

        try:
            free_path = await self._download_community_csv(
                CommunityType.FREE, self.settings.free_community_url
            )
            paid_path = await self._download_community_csv(
                CommunityType.PAID, self.settings.paid_community_url
            )

            free_members = self._load_members(free_path, CommunityType.FREE)
            paid_members = self._load_members(paid_path, CommunityType.PAID)
            if self.settings.dry_run and not free_members and not paid_members:
                notes.append("Dry run skipped live export and no existing snapshots were found.")

            if self.settings.dry_run:
                logger.info("[DRY RUN] Skipping sink writes")
                existing_states: dict[str, MemberState] = {}
                existing_ids: dict[str, str] = {}
            else:
                existing_states, existing_ids = self.sink.fetch_existing()

            new_states = self._compute_new_states(existing_states, free_members, paid_members)

            runtime = (utc_now() - started_at).total_seconds()
            metrics = self._calculate_metrics(existing_states, new_states, failed_records, runtime)

            if not self.settings.dry_run:
                self.sink.write_members(
                    list(new_states.values()),
                    existing_states=existing_states,
                    existing_ids=existing_ids,
                )
                self.sink.write_daily_metrics(metrics)

            finished_at = utc_now()
            summary = SyncSummary(
                run_id=started_at.strftime("%Y%m%d-%H%M%S"),
                started_at=started_at,
                finished_at=finished_at,
                free_members_total=metrics.free_members_total,
                paid_members_total=metrics.paid_members_total,
                new_free_members=metrics.new_free_members,
                new_paid_members=metrics.new_paid_members,
                detected_conversions=metrics.detected_conversions,
                removed_free_members=metrics.removed_free_members,
                removed_paid_members=metrics.removed_paid_members,
                failed_records=metrics.failed_records,
                runtime_seconds=metrics.runtime_seconds,
                communities=[self.settings.free_community_url, self.settings.paid_community_url],
                notes=notes,
                dry_run=self.settings.dry_run,
            )

            if not self.settings.dry_run:
                self.sink.write_sync_run(summary.__dict__)

            return summary
        except Exception as exc:
            logger.exception("Sync failed: %s", exc)
            raise
        finally:
            await self.exporter.close()
