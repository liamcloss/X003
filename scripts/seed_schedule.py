from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import httpx


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            return [row for row in reader]
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("Unsupported file type. Use CSV or JSON.")


def parse_target_regions(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [item.strip() for item in value.split(",") if item.strip()]


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/seed_schedule.py <file.csv|file.json> <api_url>")
        sys.exit(1)

    path = Path(sys.argv[1])
    api_url = sys.argv[2].rstrip("/")
    rows = load_rows(path)

    with httpx.Client(timeout=30) as client:
        for row in rows:
            topic = row.get("topic") or None
            text = row.get("text") or None
            if not topic and not text:
                raise ValueError("Each row needs either a topic or text column populated.")
            payload = {
                "topic": topic,
                "text": text,
                "tone": row.get("tone") or "neutral",
                "media_type": row.get("media_type") or None,
                "media_url": row.get("media_url") or None,
                "scheduled_time": row.get("scheduled_date") or None,
                "target_regions": parse_target_regions(row.get("target_regions")),
                "user_id": row.get("user_id") or "me",
            }
            response = client.post(f"{api_url}/queue", json=payload)
            response.raise_for_status()
            print(response.json())


if __name__ == "__main__":
    main()
