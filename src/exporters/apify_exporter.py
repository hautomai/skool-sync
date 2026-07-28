"""Apify actor-based Skool exporter.

This exporter calls the `cristiantala/skool-all-in-one-api` Apify actor with the
`members:list` action, paginates through the dataset, and writes the records to
a CSV that the rest of the pipeline (csv_parser / normalizer) can consume.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from apify_client import ApifyClientAsync

from ..config import Settings
from .base import SkoolExporter

logger = logging.getLogger("skool_sync")



class ApifySkoolExporter(SkoolExporter):
    """Downloads Skool members using an Apify actor.

    The actor is expected to be `cristiantala/skool-all-in-one-api` and the
    input follows its documented schema:
        {
            "action": "members:list",
            "groupSlug": "...",
            "email": "...",
            "password": "...",
            "params": {"all": True}
        }
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        if not self.settings.apify_api_token:
            raise ValueError("APIFY_API_TOKEN is not set.")
        self._client = ApifyClientAsync(self.settings.apify_api_token)
        self.actor_id = self.settings.apify_actor_id or "cristiantala/skool-all-in-one-api"

    @staticmethod
    def _group_slug_from_url(community_url: str) -> str:
        """Extract the group slug from a Skool community URL."""
        parsed = urlparse(community_url)
        slug = parsed.path.rstrip("/").split("/")[-1]
        if not slug:
            raise ValueError(f"Could not extract group slug from {community_url}")
        return slug

    @staticmethod
    def _normalize_record_key(key: str) -> str:
        """Map common camelCase API fields to snake_case CSV columns."""
        # Direct camelCase -> snake_case conversions
        mapping = {
            "firstName": "first_name",
            "lastName": "last_name",
            "fullName": "full_name",
            "joinedAt": "joined_at",
            "invitedBy": "invited_by",
            "skoolMemberId": "skool_member_id",
        }
        return mapping.get(key, key)

    async def export_members(
        self,
        community_url: str,
        community_type: str,
        output_path: Path,
    ) -> Path:
        group_slug = self._group_slug_from_url(community_url)

        run_input: dict = {
            "action": "members:list",
            "groupSlug": group_slug,
            "email": self.settings.skool_email,
            "password": self.settings.skool_password,
            "params": {"all": True},
        }

        logger.info("Calling Apify actor %s for groupSlug=%s", self.actor_id, group_slug)
        run = await self._client.actor(self.actor_id).call(run_input=run_input)

        # The async Apify client may return a Pydantic model or a dict.
        if hasattr(run, "default_dataset_id"):
            dataset_id = run.default_dataset_id
        elif hasattr(run, "get"):
            dataset_id = run.get("defaultDatasetId")
        else:
            dataset_id = getattr(run, "defaultDatasetId", None)
        if not dataset_id:
            raise RuntimeError(f"Apify actor run returned no dataset. Run: {run}")

        logger.info("Fetching dataset %s", dataset_id)
        dataset = await self._client.dataset(dataset_id).list_items()

        # dataset may be a Pydantic model or a dict.
        if hasattr(dataset, "items"):
            items = dataset.items
        elif isinstance(dataset, dict):
            items = dataset.get("items", [])
        else:
            items = []

        records: list[dict] = []
        for item in items:
            # The actor uses a "never throw" pattern and returns success: false
            # payloads on failures.
            if isinstance(item, dict) and item.get("success") is False:
                logger.warning("Apify actor returned failure item: %s", item)
                continue

            mapped: dict = {}
            # item may be a Pydantic model; convert it to a dict if needed.
            if not isinstance(item, dict):
                item = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for key, value in item.items():
                new_key = self._normalize_record_key(key)
                mapped[new_key] = value
            records.append(mapped)

        if not records:
            logger.warning("No valid member records returned by Apify actor for %s", group_slug)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(records).to_csv(output_path, index=False)
        logger.info("Exported %d members via Apify to %s", len(records), output_path)
        return output_path

    async def close(self) -> None:
        return None
