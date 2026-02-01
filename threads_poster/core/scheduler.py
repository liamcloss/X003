from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from threads_poster.core.config import get_settings

_settings = get_settings()
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=_settings.scheduler_timezone)
    return _scheduler
