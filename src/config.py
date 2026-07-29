"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Skool auth (used by the Apify actor)
    skool_email: str = ""
    skool_password: str = ""

    # Communities
    free_community_url: str
    paid_community_url: str

    # Google Sheets (the only supported sink in this simplified build)
    google_sheets_credentials_path: Path = Path("./data/credentials.json")
    google_sheets_spreadsheet_id: str = ""
    google_sheets_members_sheet: str = "Members"
    google_sheets_daily_metrics_sheet: str = "DailyMetrics"
    google_sheets_members_range: str = "A:Z"
    google_sheets_members_filter: str = "all"  # "all" or "converted"

    # Google OAuth 2.0 (alternative to service-account JSON)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""  # Optional: bypass the stored token file
    google_oauth_token_path: Path = Path("./data/google_oauth_token.json")

    # Apify export backend
    apify_api_token: str = ""
    apify_actor_id: str = "cristiantala/skool-all-in-one-api"

    # Skool cookie caching (used by the Apify actor)
    skool_cookies_path: Path = Path("./data/skool_cookies.json")
    skool_cookies_refresh_hours: int = 24

    # Local paths
    download_dir: Path = Path("./data/raw")
    processed_dir: Path = Path("./data/processed")
    reports_dir: Path = Path("./data/reports")

    # Operational
    timezone: str = "UTC"
    dry_run: bool = False
    backfill_mode: bool = False
    log_level: str = "INFO"

    @field_validator("free_community_url", "paid_community_url")
    @classmethod
    def _ensure_https(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith("http"):
            return f"https://www.skool.com/{value.lstrip('/')}" if "/" not in value else f"https://{value}"
        return value

    @property
    def community_urls(self) -> dict[str, str]:
        return {
            "free": str(self.free_community_url),
            "paid": str(self.paid_community_url),
        }


def get_settings() -> Settings:
    return Settings()
