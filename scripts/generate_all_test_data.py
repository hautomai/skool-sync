"""Generate three days of synthetic Skool test data with unique names.

The generator produces CSV snapshots that exercise the full sync pipeline:

    Day 1 (2026-07-29): 25,000 free + 5,000 paid + 1,000 conversions
    Day 2 (2026-07-30): +143 free, +23 paid (all 23 are conversions from the new free)
    Day 3 (2026-08-01): -25 free, -5 paid, +12 free, +9 paid (5 of the 9 are conversions)

Expected Google Sheet metrics after running Day 1 -> Day 2 -> Day 3 backfills:

    Day 1: Free=25000,  Paid=6000,  Converted=1000, Removed free=0,  Removed paid=0
    Day 2: Free=25143,  Paid=6023,  Converted=1023, Removed free=0,  Removed paid=0
    Day 3: Free=25130,  Paid=6027,  Converted=1028, Removed free=25, Removed paid=5

Usage:
    python scripts/generate_all_test_data.py [--output-dir data/raw]

The generated CSVs are written to:
    data/raw/2026-07-29-test/{free,paid}.csv
    data/raw/2026-07-30-test/{free,paid}.csv
    data/raw/2026-08-01-test/{free,paid}.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Person:
    first_name: str
    last_name: str
    id: str
    joined_at: str = "2026-07-29T00:00:00.000Z"
    membership_answers: dict[str, Any] = field(default_factory=dict)


# Stable pool of unique base names.
_FIRST_NAMES = [
    "Liam", "Olivia", "Noah", "Emma", "Oliver", "Ava", "Elijah", "Sophia",
    "Lucas", "Mia", "Mason", "Charlotte", "Ethan", "Amelia", "Logan", "Isabella",
    "James", "Harper", "Alexander", "Evelyn", "Benjamin", "Abigail", "Jacob", "Emily",
    "Michael", "Elizabeth", "Daniel", "Avery", "Henry", "Sofia", "Jackson", "Camila",
    "Aiden", "Aria", "Matthew", "Scarlett", "Joseph", "Victoria", "Samuel", "Madison",
    "Sebastian", "Luna", "David", "Grace", "Carter", "Chloe", "Wyatt", "Penelope",
    "Jayden", "Layla", "John", "Riley", "Owen", "Zoey", "Dylan", "Nora",
    "Luke", "Hannah", "Gabriel", "Lillian", "Anthony", "Addison", "Isaac", "Aubrey",
    "Grayson", "Ellie", "Jack", "Stella", "Julian", "Natalie", "Levi", "Leah",
    "Christopher", "Hazel", "Joshua", "Violet", "Andrew", "Aurora", "Lincoln", "Savannah",
]


class _NamePool:
    """Generate globally unique first+last names and IDs."""

    def __init__(self) -> None:
        self._used_keys: set[str] = set()
        self._global_counter = 0
        self._index = 0

    def take(self, count: int, prefix: str) -> list[tuple[str, str, str]]:
        """Return a list of (first, last, id) tuples with unique full names."""
        result: list[tuple[str, str, str]] = []
        while len(result) < count:
            first = _FIRST_NAMES[self._index % len(_FIRST_NAMES)]
            suffix = f"{prefix}{self._index // len(_FIRST_NAMES) + 1:03d}"
            last = f"{suffix}Last"
            key = f"{first.lower()}|{last.lower()}"
            self._index += 1
            if key in self._used_keys:
                continue
            self._used_keys.add(key)
            self._global_counter += 1
            result.append((first, last, f"{prefix}-{self._global_counter}"))
        return result


def _write_csv(path: Path, people: list[Person]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "memberId", "first_name", "last_name", "slug", "email",
        "profilePicUrl", "profilePicBubble", "bio", "location", "socialLinks",
        "level", "points", "role", "joined_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in people:
            writer.writerow({
                "id": p.id,
                "memberId": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "slug": "free",
                "email": "",
                "profilePicUrl": "",
                "profilePicBubble": "",
                "bio": "",
                "location": "",
                "socialLinks": "{}",
                "level": "1",
                "points": "0",
                "role": "member",
                "joined_at": p.joined_at,
            })


def generate_day1(pool: _NamePool, output_dir: Path) -> tuple[list[Person], list[Person]]:
    free_count = 25000
    paid_count = 5000
    overlap = 1000

    free_people = [Person(f, l, pid, "2026-07-29T00:00:00.000Z") for f, l, pid in pool.take(free_count, "free")]
    paid_people = [Person(f, l, pid, "2026-07-29T00:00:00.000Z") for f, l, pid in pool.take(paid_count, "paid")]

    # The first `overlap` free people also appear in paid.
    for p in free_people[:overlap]:
        paid_people.append(Person(p.first_name, p.last_name, p.id, "2026-07-29T00:00:00.000Z"))

    _write_csv(output_dir / "free.csv", free_people)
    _write_csv(output_dir / "paid.csv", paid_people)
    return free_people, paid_people


def generate_day2(
    pool: _NamePool,
    output_dir: Path,
    day1_free: list[Person],
    day1_paid: list[Person],
) -> tuple[list[Person], list[Person]]:
    new_free_count = 143
    new_paid_count = 23  # Total new paid rows; all are conversions from the new free members.

    new_free_people = [Person(f, l, pid, "2026-07-30T00:00:00.000Z") for f, l, pid in pool.take(new_free_count, "free")]

    # The first `new_paid_count` new free members also appear in paid.
    new_conversions = [Person(p.first_name, p.last_name, p.id, "2026-07-30T00:00:00.000Z") for p in new_free_people[:new_paid_count]]

    day2_free = day1_free + new_free_people
    day2_paid = day1_paid + new_conversions

    _write_csv(output_dir / "free.csv", day2_free)
    _write_csv(output_dir / "paid.csv", day2_paid)
    return day2_free, day2_paid


def generate_day3(
    pool: _NamePool,
    output_dir: Path,
    day2_free: list[Person],
    day2_paid: list[Person],
) -> tuple[list[Person], list[Person]]:
    removed_free = 25
    removed_paid = 5
    new_free_count = 12
    new_paid_total = 9
    new_conversions = 5

    free_only = [p for p in day2_free if p.id not in {x.id for x in day2_paid}]
    paid_only = [p for p in day2_paid if p.id not in {x.id for x in day2_free}]

    # Remove some free-only and paid-only members.
    free_to_remove = random.sample(free_only, removed_free)
    paid_to_remove = random.sample(paid_only, removed_paid)
    remove_free_ids = {p.id for p in free_to_remove}
    remove_paid_ids = {p.id for p in paid_to_remove}

    day3_free = [p for p in day2_free if p.id not in remove_free_ids]
    day3_paid = [p for p in day2_paid if p.id not in remove_paid_ids]

    # Add new free members; some become conversions, the rest stay free-only.
    new_free_people = [Person(f, l, pid, "2026-08-01T00:00:00.000Z") for f, l, pid in pool.take(new_free_count, "free")]
    new_conversions = [Person(p.first_name, p.last_name, p.id, "2026-08-01T00:00:00.000Z") for p in new_free_people[:new_conversions]]

    # Add the remaining new paid-only members.
    new_paid_only_count = new_paid_total - len(new_conversions)
    new_paid_only = [Person(f, l, pid, "2026-08-01T00:00:00.000Z") for f, l, pid in pool.take(new_paid_only_count, "paid")]

    day3_paid.extend(new_conversions)
    day3_paid.extend(new_paid_only)
    day3_free.extend(new_free_people)

    _write_csv(output_dir / "free.csv", day3_free)
    _write_csv(output_dir / "paid.csv", day3_paid)
    return day3_free, day3_paid


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Skool CSV snapshots for testing.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"), help="Directory for the generated snapshots.")
    args = parser.parse_args()

    random.seed(42)
    pool = _NamePool()
    base_dir = args.output_dir

    d1_free, d1_paid = generate_day1(pool, base_dir / "2026-07-29-test")
    d2_free, d2_paid = generate_day2(pool, base_dir / "2026-07-30-test", d1_free, d1_paid)
    d3_free, d3_paid = generate_day3(pool, base_dir / "2026-08-01-test", d2_free, d2_paid)

    for label, free, paid in [
        ("Day 1", d1_free, d1_paid),
        ("Day 2", d2_free, d2_paid),
        ("Day 3", d3_free, d3_paid),
    ]:
        overlap = len({p.id for p in free} & {p.id for p in paid})
        print(f"{label}: free={len(free)}, paid={len(paid)}, overlap={overlap}")


if __name__ == "__main__":
    main()
