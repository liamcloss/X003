from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from threads_poster.core.config import get_settings


def main() -> None:
    settings = get_settings()
    base_url = os.getenv("THREADS_API_URL", "http://localhost:8000")
    scheduled_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {
        "topic": "Digital Doofus lightning test",
        "tone": "playful",
        "scheduled_time": scheduled_time.isoformat(),
        "target_regions": ["EU"],
        "user_id": settings.threads_default_user_id,
    }
    url = f"{base_url.rstrip('/')}/queue"
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    print("Queued post for", scheduled_time.isoformat())
    print("Response:", response.json())


if __name__ == "__main__":
    main()
