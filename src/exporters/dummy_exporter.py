"""No-op exporter for backfill/testing when browser export is not needed."""

from __future__ import annotations

from pathlib import Path

from .base import SkoolExporter


class DummySkoolExporter(SkoolExporter):
    """No-op exporter."""

    async def export_members(self, community_url: str, community_type: str, output_path: Path) -> Path:
        raise NotImplementedError("Dummy exporter does not perform exports")

    async def close(self) -> None:
        return None
