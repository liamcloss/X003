from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.core.config import get_settings
from threads_poster.core.scheduler import get_scheduler
from threads_poster.db import crud
from threads_poster.db.models import Post, PostStatus
from threads_poster.db.session import SessionLocal

logger = logging.getLogger(__name__)

OPTIMAL_START = 7
OPTIMAL_END = 9

REGION_TIMEZONES = {
    "UK": "Europe/London",
    "US": "America/New_York",
}


def compute_optimal_time(target_regions: list[str]) -> datetime:
    settings = get_settings()
    now = datetime.now(UTC)
    if not target_regions:
        tz = ZoneInfo(settings.default_timezone)
        return _next_weekday_time(now, tz)

    candidate_times: list[datetime] = []
    for region in target_regions:
        tz_name = REGION_TIMEZONES.get(region.upper(), settings.default_timezone)
        tz = ZoneInfo(tz_name)
        candidate_times.append(_next_weekday_time(now, tz))
    return min(candidate_times)


def _next_weekday_time(now_utc: datetime, tz: ZoneInfo) -> datetime:
    local_now = now_utc.astimezone(tz)
    next_day = local_now
    for day_offset in range(0, 8):
        candidate = local_now + timedelta(days=day_offset)
        if candidate.weekday() == 6:
            continue
        if candidate.weekday() in (2, 3, 4) or day_offset > 0:
            next_day = candidate
            break
    scheduled_local = next_day.replace(hour=OPTIMAL_START, minute=30, second=0, microsecond=0)
    if scheduled_local <= local_now:
        scheduled_local = scheduled_local + timedelta(days=1)
    if scheduled_local.weekday() == 6:
        scheduled_local = scheduled_local + timedelta(days=1)
    return scheduled_local.astimezone(UTC)


def schedule_post(post: Post, user_id: str) -> None:
    scheduler = get_scheduler()
    scheduler.add_job(
        publish_post_job,
        trigger="date",
        run_date=post.scheduled_time,
        args=[post.id, user_id],
        id=f"publish-{post.id}",
        replace_existing=True,
    )


def publish_post_job(post_id: str, user_id: str) -> None:
    logger.info("Publishing post %s", post_id)
    client = ThreadsClient()
    with SessionLocal() as session:
        post = crud.get_post(session, post_id)
        if not post:
            logger.error("Post %s not found", post_id)
            return
        try:
            creation_id = client.create_media_container(
                user_id=user_id,
                text=post.text,
                media_type=post.media_type,
                media_url=post.media_url,
            )
            crud.update_post_status(
                session,
                post,
                status=PostStatus.scheduled,
                creation_id=creation_id,
            )
            media_id = client.publish_post(user_id=user_id, creation_id=creation_id)
            crud.update_post_status(
                session,
                post,
                status=PostStatus.published,
                media_id=media_id,
                published_time=datetime.now(UTC),
            )
            schedule_insights_followups(post.id, media_id)
        except Exception:
            logger.exception("Failed to publish post %s", post_id)
            crud.update_post_status(session, post, status=PostStatus.failed)


def schedule_insights_followups(post_id: str, media_id: str) -> None:
    scheduler = get_scheduler()
    scheduler.add_job(
        collect_insights_job,
        trigger="date",
        run_date=datetime.now(UTC) + timedelta(hours=1),
        args=[post_id, media_id],
        id=f"insights-1h-{post_id}",
        replace_existing=True,
    )
    scheduler.add_job(
        collect_insights_job,
        trigger="date",
        run_date=datetime.now(UTC) + timedelta(hours=24),
        args=[post_id, media_id],
        id=f"insights-24h-{post_id}",
        replace_existing=True,
    )


def collect_insights_job(post_id: str, media_id: str) -> None:
    logger.info("Collecting insights for %s", post_id)
    client = ThreadsClient()
    insights = client.get_post_insights(media_id, ["views", "likes", "replies", "reposts", "quotes"])
    with SessionLocal() as session:
        for metric in insights.get("data", []):
            name = metric.get("name")
            values = metric.get("values", [])
            if not values:
                continue
            value = values[-1].get("value")
            if name is None or value is None:
                continue
            crud.add_metric(session, post_id=post_id, name=name, value=float(value))
