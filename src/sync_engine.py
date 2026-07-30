"""Sync engine: orchestrates the full daily sync pipeline."""

from __future__ import annotations

import copy
import dataclasses
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
from .utils import ensure_dir, generate_key, safe_filename, utc_now

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
        # Full datetime used for member timestamps; today is kept as a date for
        # directory naming and daily metrics aggregation.
        self.run_time = (
            f"{self.today} 00:00:00"
            if run_date
            else utc_now().strftime("%Y-%m-%d %H:%M:%S")
        )
        self.snapshot_dir = ensure_dir(Path(self.settings.download_dir) / self.today)
        self._state_file = Path(self.settings.download_dir) / "state" / "member_state.json"

    def _build_exporter(self) -> SkoolExporter:
        return ApifySkoolExporter(self.settings)

    def _build_sink(self) -> Sink:
        if self.settings.dry_run:
            return NoOpSink()
        return GoogleSheetsSink(self.settings)

    def _read_local_state(self) -> dict[str, MemberState]:
        """Load the previous full member state from local JSON."""
        if not self._state_file.exists():
            return {}
        try:
            with self._state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return {key: MemberState(**value) for key, value in data.items()}
        except Exception as exc:
            logger.warning("Could not read local state from %s: %s", self._state_file, exc)
            return {}

    def _write_local_state(self, states: dict[str, MemberState]) -> None:
        """Persist the full member state to local JSON for the next run."""
        ensure_dir(self._state_file.parent)
        try:
            with self._state_file.open("w", encoding="utf-8") as f:
                json.dump(
                    {key: dataclasses.asdict(state) for key, state in states.items()},
                    f,
                    indent=2,
                    default=str,
                )
        except Exception as exc:
            logger.warning("Could not write local state to %s: %s", self._state_file, exc)

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
        """Log a warning when two active members share the same identity key."""
        from collections import Counter

        counts: dict[str, int] = Counter(m.key for m in members if m.key)
        for key, count in counts.items():
            if count > 1:
                if key.startswith("email:"):
                    logger.warning(
                        "Duplicate email key in %s community: %s appears %d times. "
                        "These rows will be merged into a single member record.",
                        community.value,
                        key,
                        count,
                    )
                else:
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
        # Warn about duplicate keys within the same community before indexing.
        self._warn_duplicate_keys(free_members, CommunityType.FREE)
        self._warn_duplicate_keys(paid_members, CommunityType.PAID)

        # Index today's members by their preferred key (member id first).
        free_by_key: dict[str, Member] = {m.key: m for m in free_members if m.key}
        paid_by_key: dict[str, Member] = {m.key: m for m in paid_members if m.key}
        all_keys = set(free_by_key.keys()) | set(paid_by_key.keys())

        # Build a secondary index using the legacy name/email fallback key so
        # existing local state and Google Sheet rows keyed by name can be
        # seamlessly migrated to the new member-id key on the first run.
        existing_by_key: dict[str, MemberState] = {}
        fallback_index: dict[str, MemberState] = {}
        for state in existing.values():
            key = state.key
            if key in existing_by_key:
                logger.warning(
                    "Duplicate key in existing data: %s. "
                    "Two different member records share the same id/name.",
                    key,
                )
            else:
                existing_by_key[key] = state

            fallback_key = generate_key(
                "",
                state.email,
                state.first_name,
                state.last_name,
            )
            if fallback_key and fallback_key not in fallback_index:
                fallback_index[fallback_key] = state

        states: dict[str, MemberState] = {}

        def _lookup_state(member: Member | None) -> MemberState | None:
            """Find an existing state by member id or legacy fallback key."""
            if member is None:
                return None
            if member.skool_member_id and member.skool_member_id in existing_by_key:
                return existing_by_key[member.skool_member_id]
            fallback_key = generate_key(
                "",
                member.email,
                member.first_name,
                member.last_name,
            )
            if fallback_key and fallback_key in fallback_index:
                return fallback_index[fallback_key]
            return None

        for key in all_keys:
            free_member = free_by_key.get(key)
            paid_member = paid_by_key.get(key)
            representative = free_member or paid_member

            # Try primary lookup on member id, then legacy fallback.
            any_member = paid_member or free_member
            state = _lookup_state(any_member)
            if state is None:
                state = MemberState(
                    skool_member_id=representative.skool_member_id if representative else "",
                    email=representative.email if representative else "",
                    first_name=representative.first_name if representative else "",
                    last_name=representative.last_name if representative else "",
                    full_name=representative.full_name if representative else "",
                )
            else:
                # Deep copy so metrics can compare the old state against the new one.
                state = copy.deepcopy(state)
                # Update name/id fields if the member's data changed.
                if representative:
                    if representative.skool_member_id:
                        state.skool_member_id = representative.skool_member_id
                    if representative.first_name:
                        state.first_name = representative.first_name
                    if representative.last_name:
                        state.last_name = representative.last_name
                    if representative.full_name:
                        state.full_name = representative.full_name

            if free_member:
                state = apply_membership(state, free_member, CommunityType.FREE, self.run_time)
            else:
                state = flag_removed(state, CommunityType.FREE, self.run_time)

            if paid_member:
                state = apply_membership(state, paid_member, CommunityType.PAID, self.run_time)
            else:
                state = flag_removed(state, CommunityType.PAID, self.run_time)

            state.last_synced_at = utc_now().isoformat()
            states[key] = state

        # Members no longer in either community should be flagged removed.
        for key, state in existing_by_key.items():
            if key not in all_keys:
                # Deep-copy so the caller's previous-state dict isn't mutated.
                state = copy.deepcopy(state)
                state = flag_removed(state, CommunityType.FREE, self.run_time)
                state = flag_removed(state, CommunityType.PAID, self.run_time)
                state.last_synced_at = utc_now().isoformat()
                states[key] = state

        return detect_conversions(states)

    def _calculate_metrics(
        self,
        previous: dict[str, MemberState],
        new_states: dict[str, MemberState],
        failed_records: int,
        runtime_seconds: float,
    ) -> DailyMetrics:
        free_active = sum(1 for s in new_states.values() if s.free_status == "active")
        paid_active = sum(1 for s in new_states.values() if s.paid_status == "active")
        converted = sum(1 for s in new_states.values() if s.current_status == "converted")

        # Removed = was active in previous state, now marked removed.
        removed_free = sum(
            1
            for key, s in new_states.items()
            if s.free_status == "removed" and key in previous and previous[key].free_status == "active"
        )
        removed_paid = sum(
            1
            for key, s in new_states.items()
            if s.paid_status == "removed" and key in previous and previous[key].paid_status == "active"
        )

        return DailyMetrics(
            date=self.today,
            free_members_total=free_active,
            paid_members_total=paid_active,
            converted_members=converted,
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

            # The sink only persists converted members (by design), so we keep a
            # full local state file to compute true incremental removals.
            previous_states = self._read_local_state()

            if self.settings.dry_run:
                logger.info("[DRY RUN] Skipping sink writes")
                sheet_existing_states: dict[str, MemberState] = {}
                existing_ids: dict[str, str] = {}
            else:
                sheet_existing_states, existing_ids = self.sink.fetch_existing()

            new_states = self._compute_new_states(previous_states, free_members, paid_members)

            runtime = (utc_now() - started_at).total_seconds()
            metrics = self._calculate_metrics(previous_states, new_states, failed_records, runtime)

            if not self.settings.dry_run:
                self.sink.write_members(
                    list(new_states.values()),
                    existing_states=sheet_existing_states,
                    existing_ids=existing_ids,
                )
                self.sink.write_daily_metrics(metrics)
                self._write_local_state(new_states)

            finished_at = utc_now()
            summary = SyncSummary(
                run_id=started_at.strftime("%Y%m%d-%H%M%S"),
                started_at=started_at,
                finished_at=finished_at,
                free_members_total=metrics.free_members_total,
                paid_members_total=metrics.paid_members_total,
                converted_members=metrics.converted_members,
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
