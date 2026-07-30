"""Generate fictive Skool community member CSVs for testing.

Usage:
    python scripts/generate_sample_csvs.py --output-dir data/raw/2026-07-30-test

The script creates:
    <output-dir>/free.csv  (default 25,000 members)
    <output-dir>/paid.csv  (default 4,000 members)

A configurable subset of members appears in both communities so the sync can
exercise free-to-paid conversion detection.
"""

from __future__ import annotations

import argparse
import csv
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIRST_NAMES = [
    "Emma", "Liam", "Olivia", "Noah", "Ava", "Oliver", "Isabella", "Elijah",
    "Sophia", "James", "Mia", "William", "Charlotte", "Benjamin", "Amelia",
    "Lucas", "Harper", "Henry", "Evelyn", "Alexander", "Abigail", "Jackson",
    "Ella", "Daniel", "Scarlett", "Matthew", "Grace", "Michael", "Chloe",
    "Ethan", "Victoria", "Samuel", "Riley", "Sebastian", "Aria", "David",
    "Lily", "Joseph", "Aubrey", "Carter", "Zoey", "Owen", "Hannah", "Wyatt",
    "Nora", "John", "Addison", "Jack", "Eleanor", "Luke", "Natalie",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
]


def _make_id() -> str:
    return uuid.uuid4().hex


def _make_slug(first: str, last: str, idx: int) -> str:
    return f"{first.lower()}-{last.lower()}-{idx}"


def _make_joined_at(days_ago: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=random.randint(0, 23))
    return dt.isoformat()


def _generate_unique_names(total: int) -> list[tuple[str, str]]:
    """Generate a list of unique first+last name pairs."""
    names: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    suffix = 1
    while len(names) < total:
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        if (first, last) in seen:
            # Append a deterministic numeric suffix to guarantee uniqueness.
            first = f"{first}{suffix}"
            suffix += 1
            if (first, last) in seen:
                continue
        seen.add((first, last))
        names.append((first, last))
    return names


def _write_csv(path: Path, persons: list[dict], community: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "id", "memberId", "first_name", "last_name", "slug", "email",
        "profilePicUrl", "profilePicBubble", "bio", "location", "socialLinks",
        "level", "points", "role", "joined_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for person in persons:
            writer.writerow({
                "id": _make_id(),
                "memberId": _make_id(),
                "first_name": person["first_name"],
                "last_name": person["last_name"],
                "slug": person["slug"],
                # Leave email empty to mimic the real Skool export the user sees.
                "email": "",
                "profilePicUrl": "",
                "profilePicBubble": "",
                "bio": "",
                "location": "",
                "socialLinks": "{}",
                "level": 1,
                "points": 0,
                "role": "member",
                "joined_at": _make_joined_at(person.get("days_ago", random.randint(1, 365))),
            })


def generate(
    output_dir: Path,
    free_count: int = 25000,
    paid_count: int = 4000,
    overlap: int = 1000,
    seed: int = 42,
) -> tuple[Path, Path]:
    random.seed(seed)

    paid_only_count = max(paid_count - overlap, 0)
    total_unique = free_count + paid_only_count
    unique_names = _generate_unique_names(total_unique)

    free_names = unique_names[:free_count]
    paid_only_names = unique_names[free_count:]

    # Build free persons with a deterministic join date.
    free_persons: list[dict] = []
    for idx, (first, last) in enumerate(free_names):
        free_persons.append({
            "first_name": first,
            "last_name": last,
            "slug": _make_slug(first, last, idx),
            "days_ago": random.randint(30, 365),
        })

    # Build paid-only persons.
    paid_persons: list[dict] = []
    for idx, (first, last) in enumerate(paid_only_names, start=free_count):
        paid_persons.append({
            "first_name": first,
            "last_name": last,
            "slug": _make_slug(first, last, idx),
            "days_ago": random.randint(1, 365),
        })

    # Overlap: members who appear in free first and then in paid.
    for free_member in free_persons[:overlap]:
        # Make sure the paid join date is more recent than the free one.
        free_days_ago = free_member["days_ago"]
        paid_days_ago = random.randint(1, max(1, free_days_ago - 1))
        paid_persons.append({
            **free_member,
            "days_ago": paid_days_ago,
        })

    free_csv = output_dir / "free.csv"
    paid_csv = output_dir / "paid.csv"
    _write_csv(free_csv, free_persons, community="free")
    _write_csv(paid_csv, paid_persons, community="paid")

    return free_csv, paid_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate fictive Skool CSVs for testing.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw/2026-07-30-test"))
    parser.add_argument("--free-count", type=int, default=25000)
    parser.add_argument("--paid-count", type=int, default=4000)
    parser.add_argument("--overlap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.overlap > args.free_count:
        raise ValueError("overlap cannot be larger than free_count")
    if args.overlap > args.paid_count:
        raise ValueError("overlap cannot be larger than paid_count")

    free_csv, paid_csv = generate(
        output_dir=args.output_dir,
        free_count=args.free_count,
        paid_count=args.paid_count,
        overlap=args.overlap,
        seed=args.seed,
    )

    print(f"Generated sample CSVs in {args.output_dir}:")
    print(f"  free: {free_csv} ({args.free_count:,} rows)")
    print(f"  paid: {paid_csv} ({args.paid_count:,} rows)")
    print(f"  overlap: {args.overlap:,} members (expected conversions)")


if __name__ == "__main__":
    main()
