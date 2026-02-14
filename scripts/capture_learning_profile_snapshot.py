from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture /content/learning-profile snapshot into JSONL."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL for the API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=14,
        help="Lookback window in days for the learning profile.",
    )
    parser.add_argument(
        "--refresh-recent",
        action="store_true",
        help="Refresh recent post insights before building profile.",
    )
    parser.add_argument(
        "--refresh-limit",
        type=int,
        default=20,
        help="Maximum recent published posts to refresh.",
    )
    parser.add_argument(
        "--user-id",
        default="me",
        help="Threads user id for refresh operations.",
    )
    parser.add_argument(
        "--out",
        default="notes/strategy/learning_profile_snapshots.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds for the learning-profile request.",
    )
    args = parser.parse_args()

    params = {
        "lookback_days": args.lookback_days,
        "refresh_recent": str(bool(args.refresh_recent)).lower(),
        "refresh_limit": args.refresh_limit,
        "user_id": args.user_id,
    }
    endpoint = f"{args.base_url.rstrip('/')}/content/learning-profile"
    request_url = f"{endpoint}?{urlencode(params)}"

    with httpx.Client(timeout=max(10.0, args.timeout_sec)) as client:
        response = client.get(endpoint, params=params)
        response.raise_for_status()
        payload = response.json()

    snapshot = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": {"url": request_url, "params": params},
        "response": payload,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=True) + "\n")

    profile = payload.get("profile", {})
    metrics = profile.get("metrics", {})
    learning = profile.get("learning", {})
    print("Snapshot captured:")
    print(f"  avg_views={metrics.get('avg_views')}")
    print(f"  median_views={metrics.get('median_views')}")
    print(f"  reply_rate={metrics.get('reply_rate')}")
    print(f"  best_hours_utc={learning.get('best_hours_utc')}")
    print(f"  top_tones={learning.get('top_tones')}")
    print(f"  out={out_path}")


if __name__ == "__main__":
    main()
