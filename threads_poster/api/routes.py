from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from threads_poster.clients.content_generator import ContentGenerator
from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.core.config import get_settings
from threads_poster.db import crud
from threads_poster.db.models import PostStatus
from threads_poster.db.session import SessionLocal
from threads_poster.core.insights import log_insight
from threads_poster.services.analytics import (
    aggregate_metrics,
    fetch_posts_with_metrics,
    refresh_follower_demographics,
)
from threads_poster.services.content_learning import (
    build_learning_profile,
    refresh_recent_post_insights,
)
from threads_poster.services.scheduling import (
    compute_optimal_time,
    reschedule_predefined_posts,
    schedule_post,
)
from threads_poster.services.auto_engagement_replier import run_auto_engagement_reply_job
from threads_poster.services.ops_automation import (
    run_capability_probe_job,
    run_kpi_snapshot_job,
)

router = APIRouter()


class QueueRequest(BaseModel):
    topic: str
    tone: str = "neutral"
    scheduled_time: datetime | None = None
    target_regions: list[str] = Field(default_factory=list)
    user_id: str


class QueueResponse(BaseModel):
    post_id: str
    scheduled_time: datetime


class RescheduleRequest(BaseModel):
    source: str = "predefined"
    min_gap_minutes: int = 30
    dry_run: bool = False


class RescheduleResponse(BaseModel):
    rescheduled: int
    skipped: int


class LearningProfileResponse(BaseModel):
    refresh: dict[str, Any]
    profile: dict[str, Any]


@router.post("/queue", response_model=QueueResponse)
async def queue_post(request: QueueRequest) -> QueueResponse:
    generator = ContentGenerator()
    generated = generator.generate_post(request.topic, request.tone)
    target_regions = request.target_regions or generated.get("target_regions") or []
    if isinstance(target_regions, str):
        target_regions = [target_regions]
    scheduled_time = _coerce_utc(request.scheduled_time or generated.get("scheduled_time"))
    now = datetime.now(UTC)
    if not scheduled_time or scheduled_time <= now:
        scheduled_time = compute_optimal_time(target_regions, user_id=request.user_id)
    if scheduled_time > now + timedelta(days=365):
        raise HTTPException(status_code=400, detail="Scheduled time exceeds 365 day limit")
    with SessionLocal() as session:
        metadata = {
            "target_regions": target_regions,
            "user_id": request.user_id,
            "source": "queue",
            "topic": request.topic,
            "tone": request.tone,
        }
        post = crud.create_post(
            session,
            text=generated["text"],
            media_type=generated["media_type"],
            media_url=generated.get("media_url"),
            scheduled_time=scheduled_time,
            metadata=metadata,
        )
        crud.update_post_status(session, post, status=PostStatus.scheduled)
        schedule_post(post, request.user_id)
        log_insight(
            "POST_SCHEDULED",
            post_id=post.id,
            user_id=request.user_id,
            scheduled_time=scheduled_time.isoformat(),
            target_regions=";".join(str(region) for region in target_regions),
        )
        return QueueResponse(post_id=post.id, scheduled_time=scheduled_time)


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.post("/reschedule", response_model=RescheduleResponse)
async def reschedule_posts(request: RescheduleRequest) -> RescheduleResponse:
    result = reschedule_predefined_posts(
        min_gap_minutes=request.min_gap_minutes,
        source_filter=request.source,
        dry_run=request.dry_run,
    )
    return RescheduleResponse(**result)


@router.post("/posts/{post_id}/publish")
async def publish_post(post_id: str, user_id: str) -> dict[str, Any]:
    client = ThreadsClient()
    with SessionLocal() as session:
        post = crud.get_post(session, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        creation_id = client.create_media_container(
            user_id=user_id,
            text=post.text,
            media_type=post.media_type,
            media_url=post.media_url,
        )
        media_id, _permalink = client.publish_post(user_id=user_id, creation_id=creation_id)
        published_time = datetime.now(UTC)
        crud.update_post_status(
            session,
            post,
            status=PostStatus.published,
            creation_id=creation_id,
            media_id=media_id,
            published_time=published_time,
        )
        metadata = post.extra_metadata or {}
        region_meta = metadata.get("target_regions")
        if isinstance(region_meta, list):
            region_meta = ";".join(str(region) for region in region_meta)
        log_insight(
            "POST_PUBLISHED",
            post_id=post.id,
            user_id=user_id,
            creation_id=creation_id,
            media_id=media_id,
            target_regions=region_meta,
            published_time=published_time.isoformat(),
        )
        return {"post_id": post.id, "media_id": media_id}


@router.get("/posts/{post_id}")
async def get_post(post_id: str) -> dict[str, object]:
    with SessionLocal() as session:
        data = fetch_posts_with_metrics(session, post_id)
        if not data:
            raise HTTPException(status_code=404, detail="Post not found")
        return data


@router.get("/insights")
async def get_insights(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    metric_name: str | None = None,
    group_by: str | None = None,
    region: str | None = None,
    user_id: str | None = None,
    refresh_demographics: bool = False,
) -> dict[str, object]:
    with SessionLocal() as session:
        demographics: list[dict[str, object]] | None = None
        if refresh_demographics:
            if not user_id:
                raise HTTPException(status_code=400, detail="user_id is required to refresh demographics")
            demographics = refresh_follower_demographics(session, user_id)
        metrics = aggregate_metrics(
            session,
            start_date=start_date.date() if start_date else None,
            end_date=end_date.date() if end_date else None,
            metric_name=metric_name,
            group_by=group_by,
            region=region,
        )
    return {"metrics": metrics, "demographics": demographics}


@router.get("/content/learning-profile", response_model=LearningProfileResponse)
async def get_content_learning_profile(
    lookback_days: int = 14,
    refresh_recent: bool = True,
    refresh_limit: int = 20,
    user_id: str | None = None,
) -> LearningProfileResponse:
    settings = get_settings()
    resolved_user_id = user_id or settings.threads_default_user_id
    with SessionLocal() as session:
        refresh_payload: dict[str, Any] = {"requested": False}
        if refresh_recent:
            refresh_payload = refresh_recent_post_insights(
                session,
                resolved_user_id,
                limit=refresh_limit,
                lookback_days=lookback_days,
            )
        profile = build_learning_profile(session, lookback_days=lookback_days)
    return LearningProfileResponse(refresh=refresh_payload, profile=profile)


@router.post("/ops/auto-engage/run-now")
async def run_auto_engage_now() -> dict[str, Any]:
    await run_in_threadpool(run_auto_engagement_reply_job)
    return {
        "status": "ok",
        "message": (
            "Auto-engagement reply job executed. "
            "Check logs/insights.log for AUTO_ENGAGE_REPLY_RUN and logs/replies.log for reply actions."
        ),
    }


@router.post("/ops/capability-probe/run-now")
async def run_capability_probe_now() -> dict[str, Any]:
    await run_in_threadpool(run_capability_probe_job, True)
    settings = get_settings()
    mode = "read_only" if settings.ops_capability_probe_read_only else "active"
    return {
        "status": "ok",
        "message": (
            f"Capability probe executed (mode={mode}). "
            "Check notes/strategy/threads_api_verification_*.json and logs/insights.log."
        ),
    }


@router.post("/ops/kpi-snapshot/run-now")
async def run_kpi_snapshot_now() -> dict[str, Any]:
    await run_in_threadpool(run_kpi_snapshot_job, True)
    return {
        "status": "ok",
        "message": (
            "KPI snapshot job executed. "
            "Check notes/strategy/learning_profile_snapshots.jsonl and logs/insights.log."
        ),
    }
