"""No-op sink used for dry-runs.

This sink does not write anywhere, which lets users test the full pipeline
without needing Google Sheets credentials.
"""

from __future__ import annotations

from ..models import DailyMetrics, MemberState
from .base import Sink


class NoOpSink(Sink):
    """Sink that discards all writes."""

    def fetch_existing(self) -> tuple[dict[str, MemberState], dict[str, str]]:
        return {}, {}

    def write_members(
        self,
        members: list[MemberState],
        existing_states: dict[str, MemberState],
        existing_ids: dict[str, str],
    ) -> None:
        return

    def write_daily_metrics(self, metrics: DailyMetrics) -> None:
        return

    def write_sync_run(self, summary: dict) -> None:
        return
