from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)
EXPECTED_FIELDS = [
    "topic",
    "tone",
    "text",
    "media_type",
    "media_url",
    "scheduled_time",
    "target_regions",
]


def read_rows(path: Path) -> Sequence[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_FIELDS:
            raise ValueError(
                f"{path} header mismatch, got {reader.fieldnames!r} expected {EXPECTED_FIELDS}"
            )
        return tuple(reader)


def normalize_timestamp(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""

    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        parsed = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def sanitize_rows(rows: Iterable[dict[str, str]]) -> Sequence[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for row in rows:
        if all(row.get(field, "").strip().lower() == field for field in EXPECTED_FIELDS):
            logger.debug("Skipping repeated header row")
            continue
        sanitized.append(
            {
                "topic": row.get("topic", "").strip(),
                "tone": row.get("tone", "").strip(),
                "text": row.get("text", "").strip(),
                "media_type": row.get("media_type", "TEXT").strip() or "TEXT",
                "media_url": (row.get("media_url") or "").strip(),
                "scheduled_time": normalize_timestamp(row.get("scheduled_time", "")),
                "target_regions": row.get("target_regions", "").strip(),
            }
        )
    return tuple(sanitized)


def write_rows(path: Path, rows: Sequence[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def report(rows: Sequence[dict[str, str]]) -> None:
    logger.info("Processed %d rows", len(rows))
    missing_schedule = [row for row in rows if not row["scheduled_time"]]
    if missing_schedule:
        logger.warning("%d rows are missing scheduled_time", len(missing_schedule))
    duplicates = {}
    for idx, row in enumerate(rows):
        key = (row["topic"], row["tone"], row["scheduled_time"])
        duplicates.setdefault(key, []).append(idx + 2)
    dups = [loc for loc in duplicates.values() if len(loc) > 1]
    if dups:
        logger.warning("Found %d duplicate entries", sum(len(group) for group in dups))
        for group in dups:
            logger.debug("Duplicate at lines %s", group)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate predefined_posts.csv")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "examples" / "predefined_posts.csv",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Rewrite the CSV with normalized timestamps and trimmed fields",
    )
    args = parser.parse_args()

    if not args.path.exists():
        raise SystemExit(f"{args.path} does not exist")

    rows = read_rows(args.path)
    cleaned = sanitize_rows(rows)
    report(cleaned)

    if args.fix:
        write_rows(args.path, cleaned)
        logger.info("Rewrote %s with normalized rows", args.path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
