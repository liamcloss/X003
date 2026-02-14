from __future__ import annotations

from collections import Counter
import logging
import random
from datetime import UTC, datetime, timedelta
from json import dumps

import os
from sqlalchemy import select

from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.core.config import get_settings, Settings
from threads_poster.core.insights import log_insight
from threads_poster.core.scheduler import get_scheduler
from threads_poster.db import crud
from threads_poster.db.models import Metric, Post, PostStatus
from threads_poster.db.session import SessionLocal

logger = logging.getLogger(__name__)
scheduled_logger = logging.getLogger("threads_poster.scheduled_posts")

THREADS_BASE_URL = "https://www.threads.com"

DEFAULT_CATCH_UP_INTERVAL_MINUTES = 30
DEFAULT_CATCH_UP_BATCH_SIZE = 6
DEFAULT_CATCH_UP_STAGGER_MINUTES = 3
DEFAULT_RESCHEDULE_MIN_GAP_MINUTES = 30
CATCH_UP_JOB_ID = "threads-catch-up"

REGIONAL_WINDOWS_UTC = {
    "EU": [(7 * 60 + 30, 9 * 60), (18 * 60, 20 * 60)],
    "US_EAST": [(12 * 60, 14 * 60), (22 * 60, 23 * 60 + 59)],
    "US_WEST": [(15 * 60, 17 * 60), (1 * 60, 3 * 60)],
}
REGION_ALIASES = {
    "UK": "EU",
    "EUROPE": "EU",
    "US": "US_EAST",
    "USA": "US_EAST",
    "AMERICA": "US_EAST",
    "US_E": "US_EAST",
    "US_EAST": "US_EAST",
    "USEAST": "US_EAST",
    "US_W": "US_WEST",
    "US_WEST": "US_WEST",
    "USWEST": "US_WEST",
}

DEFAULT_QUEUE_MIN_GAP_MINUTES = 90
DEFAULT_QUEUE_DAILY_CAP = 4
DEFAULT_QUEUE_WEEKEND_CAP = 2
DEFAULT_QUEUE_LEAD_MINUTES = 5
DEFAULT_QUEUE_LOOKAHEAD_DAYS = 30
DEFAULT_QUEUE_DEFAULT_REGION = "US_EAST"
DEFAULT_SLOT_INTERVAL_MINUTES = 15
DEFAULT_METRIC_LOOKBACK_DAYS = 14
DEFAULT_METRIC_TOP_HOURS = 3

_MIN_DELTA = timedelta(seconds=1)


def compute_optimal_time(target_regions: list[str], user_id: str | None = None) -> datetime:
    now = datetime.now(UTC)
    min_gap = timedelta(minutes=max(1, _queue_min_gap_minutes()))
    earliest = now + timedelta(minutes=max(0, _queue_lead_minutes()))
    regions = _normalize_target_regions(target_regions)
    if not regions:
        regions = [_default_queue_region()]
    lookahead_days = max(1, _queue_lookahead_days())
    weekday_cap = max(1, _queue_daily_cap())
    weekend_cap = max(1, _queue_weekend_cap())
    slot_interval = max(5, _queue_slot_interval_minutes())

    with SessionLocal() as session:
        scheduled_times = _active_scheduled_times(session, now, user_id=user_id)
        preferred_hours = _preferred_utc_hours(session, now)
    day_counts = Counter(slot.date() for slot in scheduled_times)

    candidate = _find_slot_in_windows(
        earliest=earliest,
        regions=regions,
        scheduled_times=scheduled_times,
        day_counts=day_counts,
        preferred_hours=preferred_hours,
        min_gap=min_gap,
        lookahead_days=lookahead_days,
        weekday_cap=weekday_cap,
        weekend_cap=weekend_cap,
        slot_interval_minutes=slot_interval,
    )
    if candidate:
        return candidate

    logger.warning("Falling back to non-window slot; no regional window candidate found.")
    return _find_fallback_slot(earliest, scheduled_times, min_gap, slot_interval)


def _normalize_target_regions(target_regions: list[str]) -> list[str]:
    normalized: list[str] = []
    for region in target_regions:
        tag = _normalize_region_tag(region)
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


def _normalize_region_tag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if normalized in REGIONAL_WINDOWS_UTC:
        return normalized
    return REGION_ALIASES.get(normalized)


def _default_queue_region() -> str:
    raw = os.getenv("THREADS_DEFAULT_REGION", DEFAULT_QUEUE_DEFAULT_REGION)
    return _normalize_region_tag(raw) or DEFAULT_QUEUE_DEFAULT_REGION


def _active_scheduled_times(
    session: SessionLocal,
    now: datetime,
    user_id: str | None = None,
) -> list[datetime]:
    statement = (
        select(Post.scheduled_time)
        .where(
            Post.status.in_([PostStatus.queued, PostStatus.scheduled]),
            Post.scheduled_time.is_not(None),
            Post.scheduled_time >= now - timedelta(days=1),
        )
        .order_by(Post.scheduled_time)
    )
    rows = session.scalars(statement).all()
    slots = [_ensure_utc(value) for value in rows]
    normalized = [slot for slot in slots if slot is not None]
    if not user_id:
        return normalized
    # metadata user_id filtering is skipped at SQL layer because SQLite JSON filtering is optional.
    return normalized


def _preferred_utc_hours(session: SessionLocal, now: datetime) -> set[int]:
    lookback_days = max(1, _metric_lookback_days())
    top_hours = max(1, _metric_top_hours())
    cutoff = now - timedelta(days=lookback_days)
    statement = (
        select(Post.published_time, Metric.value)
        .join(Metric, Metric.post_id == Post.id)
        .where(
            Post.published_time.is_not(None),
            Metric.name == "views",
            Metric.collected_at >= cutoff,
        )
    )
    rows = session.execute(statement).all()
    if len(rows) < 3:
        return set()
    totals: dict[int, float] = {}
    counts: dict[int, int] = {}
    for published_time, value in rows:
        published = _ensure_utc(published_time)
        if published is None:
            continue
        hour = published.hour
        totals[hour] = totals.get(hour, 0.0) + float(value or 0)
        counts[hour] = counts.get(hour, 0) + 1
    if not totals:
        return set()
    ranked = sorted(
        totals,
        key=lambda hour: (totals[hour] / max(1, counts[hour]), counts[hour]),
        reverse=True,
    )
    return set(ranked[:top_hours])


def _find_slot_in_windows(
    earliest: datetime,
    regions: list[str],
    scheduled_times: list[datetime],
    day_counts: Counter,
    preferred_hours: set[int],
    min_gap: timedelta,
    lookahead_days: int,
    weekday_cap: int,
    weekend_cap: int,
    slot_interval_minutes: int,
) -> datetime | None:
    rounded_earliest = _round_up_to_interval(earliest, slot_interval_minutes)
    min_seconds = min_gap.total_seconds()
    for offset in range(0, lookahead_days + 1):
        day = (rounded_earliest + timedelta(days=offset)).date()
        day_cap = _day_cap(day, weekday_cap, weekend_cap)
        if day_counts.get(day, 0) >= day_cap:
            continue
        day_earliest = rounded_earliest if offset == 0 else None
        for region in regions:
            windows = REGIONAL_WINDOWS_UTC.get(region, [])
            fallback: datetime | None = None
            for start_dt, end_dt in _window_bounds(day, windows):
                for candidate in _enumerate_window_candidates(
                    start_dt=start_dt,
                    end_dt=end_dt,
                    earliest=day_earliest,
                    slot_interval_minutes=slot_interval_minutes,
                ):
                    if _has_gap_conflict(candidate, scheduled_times, min_seconds):
                        continue
                    if candidate.hour in preferred_hours:
                        return candidate
                    if fallback is None:
                        fallback = candidate
            if fallback is not None:
                return fallback
    return None


def _window_bounds(target_date: datetime.date, windows: list[tuple[int, int]]) -> list[tuple[datetime, datetime]]:
    bounds: list[tuple[datetime, datetime]] = []
    for start_min, end_min in windows:
        start_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            start_min // 60,
            start_min % 60,
            tzinfo=UTC,
        )
        end_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            end_min // 60,
            end_min % 60,
            tzinfo=UTC,
        )
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        bounds.append((start_dt, end_dt))
    return bounds


def _enumerate_window_candidates(
    start_dt: datetime,
    end_dt: datetime,
    earliest: datetime | None,
    slot_interval_minutes: int,
) -> list[datetime]:
    cursor = start_dt
    if earliest and earliest > cursor:
        cursor = earliest
    cursor = _round_up_to_interval(cursor, slot_interval_minutes)
    candidates: list[datetime] = []
    while cursor < end_dt:
        candidates.append(cursor)
        cursor += timedelta(minutes=slot_interval_minutes)
    return candidates


def _round_up_to_interval(value: datetime, interval_minutes: int) -> datetime:
    normalized = value.replace(second=0, microsecond=0)
    minute = normalized.minute
    remainder = minute % interval_minutes
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return normalized
    delta = interval_minutes - remainder if remainder else interval_minutes
    return normalized + timedelta(minutes=delta)


def _has_gap_conflict(
    candidate: datetime,
    scheduled_times: list[datetime],
    min_gap_seconds: float,
) -> bool:
    lower = candidate - timedelta(seconds=min_gap_seconds)
    upper = candidate + timedelta(seconds=min_gap_seconds)
    for scheduled in scheduled_times:
        if scheduled < lower:
            continue
        if scheduled > upper:
            break
        return True
    return False


def _day_cap(target_date: datetime.date, weekday_cap: int, weekend_cap: int) -> int:
    return weekend_cap if target_date.weekday() >= 5 else weekday_cap


def _find_fallback_slot(
    earliest: datetime,
    scheduled_times: list[datetime],
    min_gap: timedelta,
    slot_interval_minutes: int,
) -> datetime:
    candidate = _round_up_to_interval(earliest, slot_interval_minutes)
    min_seconds = min_gap.total_seconds()
    for _ in range(0, 2880):
        if not _has_gap_conflict(candidate, scheduled_times, min_seconds):
            return candidate
        candidate += timedelta(minutes=slot_interval_minutes)
    return candidate


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
        creation_id: str | None = None
        media_id: str | None = None
        permalink: str | None = None
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
            media_id, permalink = client.publish_post(user_id=user_id, creation_id=creation_id)
            crud.update_post_status(
                session,
                post,
                status=PostStatus.published,
                media_id=media_id,
                published_time=datetime.now(UTC),
            )
            schedule_insights_followups(post.id, media_id)
            outputs = {
                "creation_id": creation_id,
                "media_id": media_id,
                "permalink": permalink,
                "media_type": post.media_type,
                "media_url": post.media_url,
            }
            post_url = _build_post_url(post_id, user_id, media_id, permalink)
            _log_scheduled_post_event(
                logging.INFO,
                "Published post",
                post_id,
                user_id,
                post_url,
                outputs,
            )
        except Exception as exc:
            logger.exception("Failed to publish post %s", post_id)
            crud.update_post_status(session, post, status=PostStatus.failed)
            outputs = {
                "creation_id": creation_id,
                "media_id": media_id,
                "permalink": permalink,
                "media_type": post.media_type,
                "media_url": post.media_url,
                "error": str(exc),
            }
            post_url = _build_post_url(post_id, user_id, media_id, permalink)
            _log_scheduled_post_event(
                logging.ERROR,
                "Failed to publish post",
                post_id,
                user_id,
                post_url,
                outputs,
            )


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


def catch_up_scheduled_posts(
    batch_size: int | None = None,
    stagger_minutes: int | None = None,
) -> int:
    settings = get_settings()
    batch = batch_size or _catch_up_batch_size()
    stagger = max(1, stagger_minutes or _catch_up_stagger_minutes())
    now = datetime.now(UTC)
    with SessionLocal() as session:
        cadence_boundary = _next_cadence_boundary(session, now)
        statement = (
            select(Post)
            .where(
                Post.status.in_([PostStatus.queued, PostStatus.scheduled]),
                Post.scheduled_time.is_not(None),
                Post.scheduled_time <= now,
            )
            .order_by(Post.scheduled_time)
            .limit(batch)
        )
        posts = session.scalars(statement).all()
        if not posts:
            logger.debug("No stale posts found for catch-up.")
            return 0
        rescheduled = 0
        for idx, post in enumerate(posts):
            new_time = now + timedelta(minutes=1 + stagger * idx)
            if cadence_boundary and new_time >= cadence_boundary:
                break
            post.scheduled_time = new_time
            session.add(post)
            session.commit()
            user_id = _extract_user_id(post, settings)
            if not user_id:
                log_insight("CATCHUP_SKIPPED", post_id=post.id, reason="missing_user_id")
                continue
            schedule_post(post, user_id)
            target_regions = _serialize_regions(post.extra_metadata or {})
            log_insight(
                "CATCHUP_SCHEDULED",
                post_id=post.id,
                user_id=user_id,
                scheduled_time=new_time.isoformat(),
                target_regions=target_regions,
            )
            rescheduled += 1
        if rescheduled == 0 and cadence_boundary:
            log_insight(
                "CATCHUP_SKIPPED",
                reason="cadence_boundary_reached",
                cadence_boundary=cadence_boundary.isoformat(),
            )
        return rescheduled


def register_catch_up_job() -> None:
    scheduler = get_scheduler()
    scheduler.add_job(
        catch_up_scheduled_posts,
        trigger="interval",
        minutes=_catch_up_interval_minutes(),
        id=CATCH_UP_JOB_ID,
        replace_existing=True,
        next_run_time=datetime.now(UTC),
    )


def reschedule_predefined_posts(
    min_gap_minutes: int | None = None,
    source_filter: str = "predefined",
    dry_run: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    gap_minutes = min_gap_minutes or DEFAULT_RESCHEDULE_MIN_GAP_MINUTES
    gap = timedelta(minutes=gap_minutes)
    rescheduled = 0
    skipped = 0
    with SessionLocal() as session:
        statement = (
            select(Post)
            .where(
                Post.status.in_([PostStatus.queued, PostStatus.scheduled]),
                Post.scheduled_time.is_not(None),
                Post.scheduled_time >= now,
            )
            .order_by(Post.scheduled_time)
        )
        posts = session.scalars(statement).all()
        grouped: dict[str, list[Post]] = {}
        for post in posts:
            metadata = post.extra_metadata or {}
            if source_filter and source_filter.lower() != "any":
                if str(metadata.get("source", "")).lower() != source_filter.lower():
                    continue
            region = _extract_primary_region(metadata)
            if not region:
                skipped += 1
                continue
            grouped.setdefault(region, []).append(post)

        for region, region_posts in grouped.items():
            region_posts.sort(key=lambda post: post.scheduled_time or now)
            last_scheduled = now - gap
            window_cursor: dict[tuple[str, datetime.date], int] = {}
            for post in region_posts:
                scheduled_time = _ensure_utc(post.scheduled_time) or now
                earliest = max(now, last_scheduled + gap)
                preferred_date = max(scheduled_time.date(), earliest.date())
                window_index = window_cursor.get((region, preferred_date), 0)
                new_time = _random_time_for_region(
                    region,
                    preferred_date,
                    earliest,
                    window_index=window_index,
                )
                if not dry_run:
                    post.scheduled_time = new_time
                    session.add(post)
                    session.commit()
                    user_id = _extract_user_id(post, settings)
                    if user_id:
                        schedule_post(post, user_id)
                last_scheduled = new_time
                window_cursor[(region, new_time.date())] = window_cursor.get(
                    (region, new_time.date()),
                    0,
                ) + 1
                rescheduled += 1
    if not dry_run:
        log_insight(
            "POSTS_RESCHEDULED",
            count=rescheduled,
            skipped=skipped,
            source=source_filter,
            min_gap_minutes=gap_minutes,
        )
    return {"rescheduled": rescheduled, "skipped": skipped}


def _extract_user_id(post: Post, settings: Settings) -> str | None:
    metadata = post.extra_metadata or {}
    user_id = metadata.get("user_id")
    return user_id or settings.threads_default_user_id


def _extract_primary_region(metadata: dict) -> str | None:
    regions = metadata.get("target_regions") or []
    region_value = None
    if isinstance(regions, list) and regions:
        region_value = regions[0]
    elif isinstance(regions, str):
        region_value = regions.split(";")[0].split(",")[0]
    if not region_value:
        return None
    return _normalize_region_tag(str(region_value))


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_regions(metadata: dict) -> str:
    regions = metadata.get("target_regions") or []
    if isinstance(regions, list):
        return ";".join(str(region) for region in regions)
    if isinstance(regions, str):
        return regions
    return ""


def _catch_up_interval_minutes() -> int:
    return _parse_int(os.getenv("CATCH_UP_INTERVAL_MINUTES"), DEFAULT_CATCH_UP_INTERVAL_MINUTES)


def _catch_up_batch_size() -> int:
    return _parse_int(os.getenv("CATCH_UP_BATCH_SIZE"), DEFAULT_CATCH_UP_BATCH_SIZE)


def _catch_up_stagger_minutes() -> int:
    return _parse_int(os.getenv("CATCH_UP_STAGGER_MINUTES"), DEFAULT_CATCH_UP_STAGGER_MINUTES)


def _queue_min_gap_minutes() -> int:
    return _parse_int(
        os.getenv("THREADS_MIN_GAP_MINUTES"),
        DEFAULT_QUEUE_MIN_GAP_MINUTES,
    )


def _queue_daily_cap() -> int:
    return _parse_int(
        os.getenv("THREADS_DAILY_POST_CAP"),
        DEFAULT_QUEUE_DAILY_CAP,
    )


def _queue_weekend_cap() -> int:
    return _parse_int(
        os.getenv("THREADS_WEEKEND_POST_CAP"),
        DEFAULT_QUEUE_WEEKEND_CAP,
    )


def _queue_lead_minutes() -> int:
    return _parse_int(
        os.getenv("THREADS_SCHEDULE_LEAD_MINUTES"),
        DEFAULT_QUEUE_LEAD_MINUTES,
    )


def _queue_lookahead_days() -> int:
    return _parse_int(
        os.getenv("THREADS_SCHEDULE_LOOKAHEAD_DAYS"),
        DEFAULT_QUEUE_LOOKAHEAD_DAYS,
    )


def _queue_slot_interval_minutes() -> int:
    return _parse_int(
        os.getenv("THREADS_SCHEDULE_SLOT_INTERVAL_MINUTES"),
        DEFAULT_SLOT_INTERVAL_MINUTES,
    )


def _metric_lookback_days() -> int:
    return _parse_int(
        os.getenv("THREADS_METRIC_LOOKBACK_DAYS"),
        DEFAULT_METRIC_LOOKBACK_DAYS,
    )


def _metric_top_hours() -> int:
    return _parse_int(
        os.getenv("THREADS_METRIC_TOP_HOURS"),
        DEFAULT_METRIC_TOP_HOURS,
    )


def _next_cadence_boundary(session: SessionLocal, now: datetime) -> datetime | None:
    statement = (
        select(Post.scheduled_time)
        .where(
            Post.status.in_([PostStatus.queued, PostStatus.scheduled]),
            Post.scheduled_time.is_not(None),
            Post.scheduled_time > now,
        )
        .order_by(Post.scheduled_time)
        .limit(1)
    )
    return _ensure_utc(session.scalar(statement))


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _serialize_outputs(outputs: dict[str, object]) -> str:
    try:
        return dumps(outputs, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(outputs)


def _build_post_url(
    post_id: str,
    user_id: str | None,
    media_id: str | None,
    permalink: str | None,
) -> str:
    if permalink:
        return permalink
    if media_id:
        if user_id and user_id != "me":
            user_fragment = user_id
            if not user_fragment.startswith("@"):
                user_fragment = f"@{user_fragment}"
            return f"{THREADS_BASE_URL}/{user_fragment}/post/{media_id}"
        return f"{THREADS_BASE_URL}/post/{media_id}"
    return f"{THREADS_BASE_URL}/posts/{post_id}"


def _random_time_for_region(
    region: str,
    preferred_date: datetime.date,
    earliest: datetime,
    window_index: int = 0,
) -> datetime:
    search_date = preferred_date
    limit = 30
    candidate_earliest = earliest
    windows = REGIONAL_WINDOWS_UTC.get(region, [])
    if not windows:
        raise SystemExit(f"Unknown region window: {region}")
    candidate_window_index = window_index % len(windows)
    for _ in range(limit):
        candidate_earliest_for_date = (
            candidate_earliest
            if candidate_earliest is not None and candidate_earliest.date() == search_date
            else None
        )
        candidate = _random_time_for_date(
            region,
            search_date,
            candidate_earliest_for_date,
            window_index=candidate_window_index,
        )
        if candidate:
            return candidate
        search_date += timedelta(days=1)
        candidate_earliest = None
        candidate_window_index = 0
    raise SystemExit("Could not find a future slot for rescheduling within 30 days.")


def _random_time_for_date(
    region: str,
    target_date: datetime.date,
    earliest_for_date: datetime | None,
    window_index: int = 0,
) -> datetime | None:
    windows = REGIONAL_WINDOWS_UTC.get(region, [])
    if not windows:
        return None
    window_count = len(windows)
    for offset in range(window_count):
        start_min, end_min = windows[(window_index + offset) % window_count]
        start_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            start_min // 60,
            start_min % 60,
            tzinfo=UTC,
        )
        end_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            end_min // 60,
            end_min % 60,
            tzinfo=UTC,
        )
        if earliest_for_date and end_dt <= earliest_for_date:
            continue
        allowed_start = start_dt
        if earliest_for_date and earliest_for_date > start_dt:
            allowed_start = earliest_for_date + _MIN_DELTA
        if allowed_start >= end_dt:
            continue
        available_seconds = int((end_dt - allowed_start).total_seconds())
        offset_seconds = random.randint(0, available_seconds - 1) if available_seconds > 1 else 0
        return allowed_start + timedelta(seconds=offset_seconds)
    return None


def _log_scheduled_post_event(
    level: int,
    message: str,
    post_id: str,
    user_id: str,
    post_url: str,
    outputs: dict[str, object],
) -> None:
    serialized = _serialize_outputs(outputs)
    scheduled_logger.log(
        level,
        "%s post=%s user_id=%s post_url=%s outputs=%s",
        message,
        post_id,
        user_id,
        post_url,
        serialized,
    )
