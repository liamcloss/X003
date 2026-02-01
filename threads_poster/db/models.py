from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from threads_poster.db.base import Base


class PostStatus(str, enum.Enum):
    queued = "queued"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus), default=PostStatus.queued)
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creation_id: Mapped[str | None] = mapped_column(String)
    media_id: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String)
    media_url: Mapped[str | None] = mapped_column(String)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)

    metrics: Mapped[list[Metric]] = relationship("Metric", back_populates="post", cascade="all, delete-orphan")


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id: Mapped[str | None] = mapped_column(String, ForeignKey("posts.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column()
    period: Mapped[str] = mapped_column(String, default="lifetime")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)

    post: Mapped[Post] = relationship("Post", back_populates="metrics")
