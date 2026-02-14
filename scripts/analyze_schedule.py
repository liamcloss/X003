from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

EXPECTED_FIELDS = [
    "topic",
    "tone",
    "text",
    "media_type",
    "media_url",
    "scheduled_time",
    "target_regions",
]


def normalize_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        if header != EXPECTED_FIELDS:
            print(
                "Warning: header mismatch. Expected",
                EXPECTED_FIELDS,
                "but got",
                header,
            )
        for row in reader:
            if all((row.get(field, "").strip().lower() == field for field in EXPECTED_FIELDS)):
                continue
            yield {field: (row.get(field) or "").strip() for field in EXPECTED_FIELDS}


def analyze(rows: Iterable[dict[str, str]]) -> None:
    per_day = Counter()
    per_hour = Counter()
    duplicates = defaultdict(list)

    for idx, row in enumerate(rows, start=2):
        scheduled = normalize_iso(row["scheduled_time"])
        if not scheduled:
            continue
        date_key = scheduled.date().isoformat()
        hour_key = (scheduled.date().isoformat(), scheduled.hour)
        per_day[date_key] += 1
        per_hour[hour_key] += 1
        duplicates[(row["topic"], row["tone"], scheduled.isoformat())].append(idx)

    print("\nPosts per date:")
    for date, count in sorted(per_day.items()):
        print(f"  {date}: {count}")

    frequent_days = [day for day, count in per_day.items() if count > 4]
    if frequent_days:
        print("\n⚠️ Dates with more than 4 posts:")
        for day in frequent_days:
            print(f"  {day}: {per_day[day]} posts")

    crowded_hours = [slot for slot, cnt in per_hour.items() if cnt > 1]
    if crowded_hours:
        print("\n⚠️ Hours with more than one scheduled post:")
        for (day, hour) in sorted(crowded_hours):
            print(f"  {day} at {hour:02d}:00 - {per_hour[(day, hour)]} posts")

    dup_groups = [loc for loc in duplicates.values() if len(loc) > 1]
    if dup_groups:
        print("\n⚠️ Duplicate entries found (same topic/tone/time):")
        for group in dup_groups:
            print("  lines", group)
    if not per_day:
        print("No scheduled times found to analyze.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Report schedule density for predefined posts")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "examples" / "predefined_posts.csv",
    )
    args = parser.parse_args()
    if not args.path.exists():
        raise SystemExit(f"{args.path} does not exist")
    rows = tuple(load_rows(args.path))
    analyze(rows)


if __name__ == "__main__":
    main()
