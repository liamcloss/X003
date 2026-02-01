from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from threads_poster.clients.content_generator import ContentGenerator
from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.db import crud
from threads_poster.db.models import PostStatus
from threads_poster.db.session import SessionLocal
from threads_poster.services.analytics import (
    aggregate_metrics,
    fetch_posts_with_metrics,
    refresh_follower_demographics,
)
from threads_poster.services.scheduling import compute_optimal_time, schedule_post

router = APIRouter()


class QueueRequest(BaseModel):
    topic: str | None = None
    text: str | None = None
    tone: str = "neutral"
    media_type: str | None = None
    media_url: str | None = None
    scheduled_time: datetime | None = None
    target_regions: list[str] = Field(default_factory=list)
    user_id: str

    @model_validator(mode="after")
    def validate_content(self) -> "QueueRequest":
        if not self.topic and not self.text:
            raise ValueError("Either topic or text is required")
        return self


class QueueResponse(BaseModel):
    post_id: str
    scheduled_time: datetime


@router.post("/queue", response_model=QueueResponse)
async def queue_post(request: QueueRequest) -> QueueResponse:
    if request.text:
        text = request.text
        media_url = request.media_url
        media_type = request.media_type or ("IMAGE" if request.media_url else "TEXT")
    else:
        generator = ContentGenerator()
        generated = generator.generate_post(request.topic or "", request.tone)
        text = generated["text"]
        media_type = generated["media_type"]
        media_url = generated.get("media_url")
    scheduled_time = request.scheduled_time or compute_optimal_time(request.target_regions)
    if scheduled_time > datetime.now(UTC) + timedelta(days=365):
        raise HTTPException(status_code=400, detail="Scheduled time exceeds 365 day limit")
    with SessionLocal() as session:
        post = crud.create_post(
            session,
            text=text,
            media_type=media_type,
            media_url=media_url,
            scheduled_time=scheduled_time,
            metadata={"target_regions": request.target_regions},
        )
        crud.update_post_status(session, post, status=PostStatus.scheduled)
        schedule_post(post, request.user_id)
        return QueueResponse(post_id=post.id, scheduled_time=scheduled_time)


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
        media_id = client.publish_post(user_id=user_id, creation_id=creation_id)
        crud.update_post_status(
            session,
            post,
            status=PostStatus.published,
            creation_id=creation_id,
            media_id=media_id,
            published_time=datetime.now(UTC),
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
