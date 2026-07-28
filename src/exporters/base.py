"""Abstract Skool exporter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class SkoolExporter(ABC):
    """Abstract base for exporting Skool members."""

    @abstractmethod
    async def export_members(
        self,
        community_url: str,
        community_type: str,
        output_path: Path,
    ) -> Path:
        """Export members for a community and save the CSV to output_path."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up any resources."""
        ...
