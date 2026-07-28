"""Debug script: export a single Skool community via the Apify actor.

Usage:
    python scripts/debug_export.py free
    python scripts/debug_export.py paid

Requires a valid .env file with Apify token and Skool credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make src importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import get_settings
from src.exporters.apify_exporter import ApifySkoolExporter
from src.utils import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug export for one Skool community via Apify")
    parser.add_argument(
        "community",
        choices=["free", "paid"],
        help="Which community to export.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="DEBUG",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging(args.log_level)

    settings = get_settings()
    community_url = settings.community_urls[args.community]
    output_dir = Path(settings.download_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"debug_{args.community}.csv"

    exporter = ApifySkoolExporter(settings)
    try:
        downloaded = await exporter.export_members(
            community_url=community_url,
            community_type=args.community,
            output_path=output_path,
        )
        print(f"SUCCESS: exported to {downloaded}")
    except Exception as exc:
        print(f"FAILED: {exc}")
        raise
    finally:
        await exporter.close()


if __name__ == "__main__":
    asyncio.run(main())
