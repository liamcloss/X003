from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    threads_access_token: str
    threads_app_id: str
    threads_app_secret: str
    openai_api_key: str
    default_timezone: str
    database_url: str
    scheduler_timezone: str


def get_settings() -> Settings:
    return Settings(
        threads_access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
        threads_app_id=os.getenv("THREADS_APP_ID", ""),
        threads_app_secret=os.getenv("THREADS_APP_SECRET", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "UTC"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./threads.db"),
        scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "UTC"),
    )
