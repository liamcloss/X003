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


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/seed_schedule.py <file.csv|file.json> <api_url>")
        sys.exit(1)

    path = Path(sys.argv[1])
    api_url = sys.argv[2].rstrip("/")
    rows = load_rows(path)

    with httpx.Client(timeout=30) as client:
        for row in rows:
            payload = {
                "topic": row["topic"],
                "tone": row.get("tone") or "neutral",
                "scheduled_time": row.get("scheduled_date"),
                "target_regions": json.loads(row["target_regions"]) if row.get("target_regions") else [],
                "user_id": row.get("user_id") or "me",
            }
            response = client.post(f"{api_url}/queue", json=payload)
            response.raise_for_status()
            print(response.json())


if __name__ == "__main__":
    main()
