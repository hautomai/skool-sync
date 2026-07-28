"""Abstract sink interface for member data and daily metrics."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DailyMetrics, MemberState


class Sink(ABC):
    """Output destination for member state and metrics."""

    @abstractmethod
    def fetch_existing(self) -> tuple[dict[str, MemberState], dict[str, str]]:
        """Return (identity_key -> MemberState, identity_key -> row record ID)."""
        ...

    @abstractmethod
    def write_members(
        self,
        members: list[MemberState],
        existing_states: dict[str, MemberState],
        existing_ids: dict[str, str],
    ) -> None:
        """Write member state records using the provided existing state/ID maps."""
        ...

    @abstractmethod
    def write_daily_metrics(self, metrics: DailyMetrics) -> None:
        """Write a daily metrics row."""
        ...

    @abstractmethod
    def write_sync_run(self, summary: dict) -> None:
        """Write sync run metadata."""
        ...
