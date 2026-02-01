from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from threads_poster.db.models import Metric, Post, PostStatus


def create_post(
    session: Session,
    text: str,
    media_type: str,
    media_url: str | None,
    scheduled_time: datetime | None,
    metadata: dict | None = None,
) -> Post:
    post = Post(
        text=text,
        media_type=media_type,
        media_url=media_url,
        scheduled_time=scheduled_time,
        extra_metadata=metadata or {},
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def update_post_status(
    session: Session,
    post: Post,
    status: PostStatus,
    creation_id: str | None = None,
    media_id: str | None = None,
    published_time: datetime | None = None,
) -> Post:
    post.status = status
    if creation_id is not None:
        post.creation_id = creation_id
    if media_id is not None:
        post.media_id = media_id
    if published_time is not None:
        post.published_time = published_time
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


def get_post(session: Session, post_id: str) -> Post | None:
    return session.get(Post, post_id)


def list_posts(session: Session) -> list[Post]:
    return list(session.scalars(select(Post)).all())


def add_metric(
    session: Session,
    post_id: str | None,
    name: str,
    value: float,
    period: str = "lifetime",
    metadata: dict | None = None,
) -> Metric:
    metric = Metric(
        post_id=post_id,
        name=name,
        value=value,
        period=period,
        extra_metadata=metadata or {},
    )
    session.add(metric)
    session.commit()
    session.refresh(metric)
    return metric


def list_metrics_for_post(session: Session, post_id: str) -> list[Metric]:
    statement = select(Metric).where(Metric.post_id == post_id)
    return list(session.scalars(statement).all())
