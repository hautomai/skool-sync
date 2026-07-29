"""Apify actor-based Skool exporter.

This exporter calls the `cristiantala/skool-all-in-one-api` Apify actor with the
`members:list` action, paginates through the dataset, and writes the records to
a CSV that the rest of the pipeline (csv_parser / normalizer) can consume.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
from apify_client import ApifyClientAsync

from ..config import Settings
from ..cookie_manager import SkoolCookieManager
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
        self._cookie_manager = SkoolCookieManager(
            self.settings.skool_cookies_path,
            refresh_hours=self.settings.skool_cookies_refresh_hours,
        )

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

    async def _run_actor(self, run_input: dict) -> Any:
        """Call the Apify actor and return the raw run object."""
        logger.info("Calling Apify actor %s with action=%s", self.actor_id, run_input.get("action"))
        return await self._client.actor(self.actor_id).call(run_input=run_input)

    async def _fetch_dataset_items(self, dataset_id: str) -> list[Any]:
        """Fetch all items from a dataset."""
        logger.info("Fetching dataset %s", dataset_id)
        dataset = await self._client.dataset(dataset_id).list_items()
        if hasattr(dataset, "items"):
            return dataset.items
        if isinstance(dataset, dict):
            return dataset.get("items", [])
        return []

    async def _login_with_cookies(self) -> list[dict[str, Any]] | None:
        """Call auth:login and cache the returned cookies.

        Raises RuntimeError if the actor reports a login failure.
        """
        logger.info("Refreshing Skool session cookies via auth:login")
        run = await self._run_actor(
            {
                "action": "auth:login",
                "email": self.settings.skool_email,
                "password": self.settings.skool_password,
            }
        )
        dataset_id = self._extract_dataset_id(run)
        if not dataset_id:
            return None

        for item in await self._fetch_dataset_items(dataset_id):
            if not isinstance(item, dict):
                item = item.model_dump() if hasattr(item, "model_dump") else dict(item)

            if item.get("success") is False:
                raise RuntimeError(
                    f"Skool auth:login failed: {item.get('message', item)}"
                )

            if "cookies" in item:
                # Use the actor-reported expiry if available, otherwise rely on
                # the configured refresh window.
                expires_at: int | None = None
                raw_expires = item.get("expiresAt")
                if isinstance(raw_expires, (int, float)):
                    expires_at = int(raw_expires)
                elif isinstance(raw_expires, str):
                    try:
                        expires_dt = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
                        expires_at = int(expires_dt.timestamp())
                    except ValueError:
                        expires_at = None

                cookies = item["cookies"]
                self._cookie_manager.save(cookies, expires_at=expires_at)
                return cookies

        logger.warning("auth:login did not return cookies")
        return None

    @staticmethod
    def _extract_dataset_id(run: Any) -> str | None:
        """Return the default dataset id from an Apify run response."""
        if hasattr(run, "default_dataset_id"):
            return run.default_dataset_id
        if hasattr(run, "get"):
            return run.get("defaultDatasetId")
        return getattr(run, "defaultDatasetId", None)

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
            "params": {"all": True},
        }

        # Try cached cookies first, otherwise obtain fresh ones, otherwise fall
        # back to sending the email/password directly.
        cookies = self._cookie_manager.load()
        if cookies:
            run_input["cookies"] = cookies
        elif self.settings.skool_email and self.settings.skool_password:
            fresh_cookies = await self._login_with_cookies()
            if fresh_cookies:
                run_input["cookies"] = fresh_cookies
            else:
                run_input["email"] = self.settings.skool_email
                run_input["password"] = self.settings.skool_password
        else:
            raise ValueError("No cached Skool cookies and SKOOL_EMAIL/SKOOL_PASSWORD are not set.")

        run = await self._run_actor(run_input)
        dataset_id = self._extract_dataset_id(run)
        if not dataset_id:
            raise RuntimeError(f"Apify actor run returned no dataset. Run: {run}")

        items = await self._fetch_dataset_items(dataset_id)

        # If every item is an auth failure and we used cookies, the cached
        # session is probably stale. Refresh once and retry.
        if run_input.get("cookies") and self._all_items_are_auth_failures(items):
            logger.warning("members:list failed with cached cookies; refreshing and retrying")
            self._cookie_manager.clear()
            fresh_cookies = await self._login_with_cookies()
            if fresh_cookies:
                run_input["cookies"] = fresh_cookies
                # Remove credentials fallback if it was added.
                run_input.pop("email", None)
                run_input.pop("password", None)
            else:
                run_input["email"] = self.settings.skool_email
                run_input["password"] = self.settings.skool_password

            run = await self._run_actor(run_input)
            dataset_id = self._extract_dataset_id(run)
            if not dataset_id:
                raise RuntimeError(f"Apify actor run returned no dataset. Run: {run}")
            items = await self._fetch_dataset_items(dataset_id)

        records: list[dict] = []
        for item in items:
            if isinstance(item, dict) and item.get("success") is False:
                logger.warning("Apify actor returned failure item: %s", item)
                continue

            mapped: dict = {}
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

    @staticmethod
    def _all_items_are_auth_failures(items: list[Any]) -> bool:
        """Return True when all dataset items are explicit auth/permission failures."""
        if not items:
            return False
        for item in items:
            if isinstance(item, dict) and item.get("success") is False:
                continue
            return False
        return True

    async def close(self) -> None:
        return None
