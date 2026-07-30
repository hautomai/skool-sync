"""Generate a Day 3 test dataset based on Day 2 with removals and conversions."""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Day 3 test dataset")
    parser.add_argument("--day2-dir", default="data/raw/2026-07-31", help="Path to Day 2 CSVs")
    parser.add_argument("--output-dir", default="data/raw/2026-08-01", help="Output directory")
    args = parser.parse_args()

    day2_dir = Path(args.day2_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    free_rows = _read_csv(day2_dir / "free.csv")
    paid_rows = _read_csv(day2_dir / "paid.csv")

    paid_ids = {r["id"] for r in paid_rows}
    free_only = [r for r in free_rows if r["id"] not in paid_ids]
    paid_only = [r for r in paid_rows if r["id"] not in {r["id"] for r in free_rows}]

    # Removals
    remove_free = random.sample(free_only, 25)
    remove_free_ids = {r["id"] for r in remove_free}
    remove_paid = random.sample(paid_only, 5)
    remove_paid_ids = {r["id"] for r in remove_paid}

    new_free_rows = [r for r in free_rows if r["id"] not in remove_free_ids]
    new_paid_rows = [r for r in paid_rows if r["id"] not in remove_paid_ids]

    # 5 conversions: existing free members who now also join paid
    # Exclude any free member whose name collides with an existing paid member.
    paid_name_keys = {_name_key(r) for r in new_paid_rows}
    conversion_candidates = [
        r for r in new_free_rows
        if r["id"] not in paid_ids and _name_key(r) not in paid_name_keys
    ]
    conversions = random.sample(conversion_candidates, 5)

    # 12 new free members and 4 new paid-only members with unique names.
    new_free_members = _generate_new_members(12, "free", paid_name_keys | {_name_key(r) for r in new_free_rows})
    new_paid_only_members = _generate_new_members(4, "paid", paid_name_keys)

    new_free_rows.extend(new_free_members)
    # Conversions are already in the free list.

    new_paid_rows.extend(conversions)
    new_paid_rows.extend(new_paid_only_members)

    random.shuffle(new_free_rows)
    random.shuffle(new_paid_rows)

    _write_csv(output_dir / "free.csv", new_free_rows, list(free_rows[0].keys()))
    _write_csv(output_dir / "paid.csv", new_paid_rows, list(paid_rows[0].keys()))

    print(f"Generated {output_dir}")
    print(f"  Free rows:  {len(free_rows)} -> {len(new_free_rows)} ({len(new_free_rows) - len(free_rows)} net)")
    print(f"  Paid rows:  {len(paid_rows)} -> {len(new_paid_rows)} ({len(new_paid_rows) - len(paid_rows)} net)")
    print(f"  Removed free:  {len(remove_free)}")
    print(f"  Removed paid:  {len(remove_paid)}")
    print(f"  Conversions:   {len(conversions)}")


def _name_key(row: dict[str, str]) -> str:
    return f"{row.get('first_name', '').strip().lower()}|{row.get('last_name', '').strip().lower()}"


def _generate_new_members(count: int, community: str, used_name_keys: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    base_names = [
        ("Liam", "Smith"), ("Olivia", "Johnson"), ("Noah", "Williams"), ("Emma", "Brown"),
        ("Oliver", "Jones"), ("Ava", "Garcia"), ("Elijah", "Miller"), ("Sophia", "Davis"),
        ("Lucas", "Rodriguez"), ("Mia", "Martinez"), ("Mason", "Hernandez"), ("Charlotte", "Lopez"),
    ]
    i = 0
    while len(rows) < count:
        first, last = base_names[i % len(base_names)]
        suffix = f"{community}{i+1:03d}"
        key = f"{first.lower()}|{last.lower()}{suffix}"
        i += 1
        if key in used_name_keys:
            continue
        used_name_keys.add(key)
        rows.append(
            {
                "id": f"new-{community}-{i}",
                "memberId": f"new-{community}-{i}",
                "first_name": f"{first}{suffix}",
                "last_name": f"{last}{suffix}",
                "slug": community,
                "email": "",
                "profilePicUrl": "",
                "profilePicBubble": "",
                "bio": "",
                "location": "",
                "socialLinks": "{}",
                "level": "1",
                "points": "0",
                "role": "member",
                "joined_at": "2026-08-01T00:00:00.000Z",
            }
        )
    return rows


if __name__ == "__main__":
    main()
