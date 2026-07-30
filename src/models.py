"""Domain models for the Skool sync pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .utils import generate_key


class CommunityType(str, Enum):
    FREE = "free"
    PAID = "paid"


class MemberStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"


class LifecycleStatus(str, Enum):
    FREE_ONLY = "free_only"
    PAID_ONLY = "paid_only"
    BOTH = "both"
    CONVERTED = "converted"


@dataclass
class Member:
    """Normalized member record produced from a raw Skool CSV row."""

    community_type: CommunityType
    community_name: str
    community_slug: str
    source_file: str
    imported_at: datetime
    snapshot_date: str
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    joined_at: str = ""
    invited_by: str = ""
    membership_answers: dict[str, Any] = field(default_factory=dict)
    skool_member_id: str = ""
    profile_pic_url: str = ""
    raw_record: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return generate_key(
            self.profile_pic_url, self.email, self.first_name, self.last_name
        )


@dataclass
class MemberState:
    """Aggregated state of a unique member across communities."""

    skool_member_id: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    free_status: str = MemberStatus.REMOVED.value
    paid_status: str = MemberStatus.REMOVED.value
    free_joined_at: str = ""
    paid_joined_at: str = ""
    free_left_at: str = ""
    paid_left_at: str = ""
    first_seen_free_at: str = ""
    first_seen_paid_at: str = ""
    conversion_detected_at: str = ""
    current_status: str = LifecycleStatus.FREE_ONLY.value
    membership_answers: dict[str, Any] = field(default_factory=dict)
    free_source_file: str = ""
    paid_source_file: str = ""
    profile_pic_url: str = ""
    last_synced_at: str = ""

    @property
    def key(self) -> str:
        return generate_key(
            self.profile_pic_url, self.email, self.first_name, self.last_name
        )


@dataclass
class DailyMetrics:
    """Aggregated daily metrics written to the DailyMetrics table/sheet."""

    date: str
    free_members_total: int = 0
    paid_members_total: int = 0
    converted_members: int = 0
    removed_free_members: int = 0
    removed_paid_members: int = 0
    failed_records: int = 0
    runtime_seconds: float = 0.0
    snapshot_date: str = ""


@dataclass
class SyncSummary:
    """Output summary generated after every sync run."""

    run_id: str
    started_at: datetime
    finished_at: datetime
    free_members_total: int = 0
    paid_members_total: int = 0
    converted_members: int = 0
    removed_free_members: int = 0
    removed_paid_members: int = 0
    failed_records: int = 0
    runtime_seconds: float = 0.0
    communities: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False
