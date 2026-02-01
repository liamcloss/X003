from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.db.models import Metric, Post


def aggregate_metrics(
    session: Session,
    start_date: date | None = None,
    end_date: date | None = None,
    metric_name: str | None = None,
    group_by: str | None = None,
    region: str | None = None,
) -> list[dict[str, object]]:
    statement: Select[tuple[Metric]] = select(Metric)
    if metric_name:
        statement = statement.where(Metric.name == metric_name)
    if start_date:
        statement = statement.where(Metric.collected_at >= start_date)
    if end_date:
        statement = statement.where(Metric.collected_at <= end_date)

    rows = session.scalars(statement).all()
    rows = _filter_region(rows, region)
    grouped: dict[tuple[str, str | None], float] = defaultdict(float)
    for metric in rows:
        period_key = _period_key(metric.collected_at.date(), group_by)
        grouped[(metric.name, period_key)] += metric.value
    return [
        {"name": name, "period": period, "value": value}
        for (name, period), value in sorted(grouped.items(), key=lambda item: item[0])
    ]


def _period_key(metric_date: date, group_by: str | None) -> str | None:
    if group_by == "day":
        return metric_date.isoformat()
    if group_by == "week":
        year, week, _ = metric_date.isocalendar()
        return f"{year}-W{week:02d}"
    if group_by == "month":
        return f"{metric_date.year}-{metric_date.month:02d}"
    return None


def _filter_region(rows: Iterable[Metric], region: str | None) -> list[Metric]:
    if not region:
        return list(rows)
    region_upper = region.upper()
    return [
        metric
        for metric in rows
        if metric.extra_metadata and metric.extra_metadata.get("region", "").upper() == region_upper
    ]


def fetch_posts_with_metrics(session: Session, post_id: str) -> dict[str, object] | None:
    post = session.get(Post, post_id)
    if not post:
        return None
    return {
        "id": post.id,
        "status": post.status.value,
        "scheduled_time": post.scheduled_time,
        "published_time": post.published_time,
        "creation_id": post.creation_id,
        "media_id": post.media_id,
        "text": post.text,
        "media_type": post.media_type,
        "media_url": post.media_url,
        "metrics": [
            {
                "name": metric.name,
                "value": metric.value,
                "period": metric.period,
                "collected_at": metric.collected_at,
            }
            for metric in post.metrics
        ],
    }


def refresh_follower_demographics(session: Session, user_id: str) -> list[dict[str, object]]:
    client = ThreadsClient()
    insights = client.get_user_insights(user_id, ["follower_demographics"], breakdown="country")
    stored: list[dict[str, object]] = []
    for metric in insights.get("data", []):
        if metric.get("name") != "follower_demographics":
            continue
        for value in metric.get("values", []):
            breakdowns = value.get("value", [])
            for entry in breakdowns:
                region = entry.get("country")
                count = entry.get("value")
                if region is None or count is None:
                    continue
                stored.append({"name": "follower_demographics", "region": region, "value": count})
                session.add(
                    Metric(
                        post_id=None,
                        name="follower_demographics",
                        value=float(count),
                        period="lifetime",
                        extra_metadata={"region": region},
                    )
                )
    if stored:
        session.commit()
    return stored
