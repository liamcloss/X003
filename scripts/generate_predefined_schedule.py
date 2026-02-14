from __future__ import annotations

import ast
import os
import re
import sys

import argparse
import csv
import json
import logging
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import httpx
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from threads_poster.core.config import get_settings
from threads_poster.db.models import Metric, Post, PostStatus
from threads_poster.db.session import SessionLocal
from threads_poster.services.news_fetcher import fetch_edge_items
from threads_poster.core.insights import log_insight
from threads_poster.services.content_quality import (
    SIMILARITY_THRESHOLD_DEFAULT,
    max_similarity,
    quality_issues,
    sanitize_text,
)

REGIONS = ["EU", "US_EAST", "US_WEST"]
REGIONAL_WINDOWS = {
    "EU": [(7 * 60 + 30, 9 * 60), (18 * 60, 20 * 60)],
    "US_EAST": [(12 * 60, 14 * 60), (22 * 60, 23 * 60 + 59)],
    "US_WEST": [(15 * 60, 17 * 60), (1 * 60, 3 * 60)],
}
ALLOWED_TONES = {
    "playful",
    "thoughtful",
    "curious",
    "lightly-contrarian",
    "motivational",
    "whimsical",
    "witty",
    "absurd",
}

logger = logging.getLogger(__name__)
openai_logger = logging.getLogger("threads_poster.openai")

CSV_PATH = Path(__file__).resolve().parents[1] / "examples" / "predefined_posts.csv"
CSV_PATH = Path(os.getenv("PREDEFINED_POSTS_CSV", CSV_PATH))
EXPECTED_FIELDS = [
    "topic",
    "tone",
    "text",
    "media_type",
    "media_url",
    "scheduled_time",
    "target_regions",
]
MAX_TEXT_LENGTH = 500
TINYURL_RATIO = 0.0
TINYURL_ENDPOINT = "https://tinyurl.com/api-create.php"
DEFAULT_OVERSAMPLE_FACTOR = 2
DEFAULT_REWRITE_BUDGET = 6
DEFAULT_LEARNING_LOOKBACK_DAYS = 14
DEFAULT_NOVELTY_LOOKBACK_DAYS = 30
DEFAULT_NOVELTY_CORPUS_LIMIT = 400
DEFAULT_COMMENTARY_RATIO = 1 / 3
DEFAULT_MIN_SPECIFIC_PROMPT_RATIO = 0.7
LOW_REPLY_RATE_THRESHOLD = 0.015
LOW_REPLY_SPECIFIC_PROMPT_RATIO = 0.8
COMMENTARY_TONE_HINTS = {"thoughtful", "curious", "lightly-contrarian", "motivational"}
WHIMSICAL_TONE_HINTS = {"playful", "whimsical", "witty", "absurd"}
GENERIC_QUESTION_STRINGS = {
    "thoughts",
    "any thoughts",
    "what do you think",
    "what are your thoughts",
    "agree",
    "disagree",
    "agree or disagree",
    "your take",
    "what is your take",
    "yes or no",
    "anyone else",
}
SPECIFIC_QUESTION_SIGNAL_TERMS = {
    "one",
    "first",
    "biggest",
    "smallest",
    "most",
    "least",
    "today",
    "week",
    "month",
    "step",
    "move",
    "tradeoff",
    "risk",
    "bet",
    "next",
}
SPECIFIC_PROMPT_TEMPLATES_TOPIC = [
    "What part of {topic} would you test first in real life?",
    "What is one move you would make differently on {topic} this week?",
]
SPECIFIC_PROMPT_TEMPLATES_GENERIC = [
    "What is one part of this idea you would try in your own workflow this week?",
    "What would you test first if this showed up in your day tomorrow?",
    "Where do you see the biggest practical upside here?",
    "What is one risk you would watch first before committing to it?",
    "If you had to pick one action from this, what would it be?",
]
DEFAULT_CONTENT_NICHE = "ai_work_reality_check"
AI_WORK_NICHE_TERMS = {
    "ai",
    "assistant",
    "workflow",
    "inbox",
    "email",
    "meeting",
    "calendar",
    "deadline",
    "support",
    "customer",
    "creator",
    "team",
    "productivity",
    "automation",
    "app",
    "tool",
}
AI_WORK_SETUP_TEMPLATES = [
    "{topic} is shifting from novelty to daily workflow, and the real tension is speed versus judgment.",
    "{topic} is getting embedded into normal work, but quality still depends on where humans stay in the loop.",
    "{topic} looks efficient on paper, yet the trust gap shows up when people skip verification.",
    "{topic} can cut repetitive work, but only if teams define where automation stops.",
]
AI_WORK_QUESTION_TEMPLATES = [
    "What is one step you would automate first, and one step you would keep human?",
    "Where would you put the first human checkpoint before this scales?",
    "What tradeoff would you accept here: speed, quality, or trust?",
    "What is one workflow where this saves real time for you this week?",
]
CONTEXTLESS_OPENERS = {
    "it",
    "this",
    "that",
    "they",
    "these",
    "those",
    "he",
    "she",
}
GENERIC_FILLER_PHRASES = {
    "weird details shape the bigger story",
    "tiny glitch in reality",
}
TOPIC_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "about",
    "this",
    "that",
    "your",
    "you",
    "into",
    "over",
    "under",
    "around",
    "more",
    "less",
    "what",
    "when",
    "where",
    "which",
    "while",
    "how",
    "why",
}
NICHE_TOPIC_TERMS = {
    "mummy",
    "iguana",
    "grain elevator",
    "longest nose",
    "sausage-shaped",
    "oumuamua",
}
CLICHE_PHRASES = {
    "secret life",
    "mind of its own",
    "judging my",
    "if trees could talk",
    "wandering zombie",
    "sometimes i wonder",
    "ever notice",
    "social experiments in patience",
    "mastering the art of pretending",
}
RELATABLE_CONTEXT_TERMS = {
    "inbox",
    "email",
    "group chat",
    "notification",
    "meeting",
    "calendar",
    "deadline",
    "workflow",
    "customer",
    "support",
    "travel",
    "app",
    "ai",
    "assistant",
    "creator",
    "subscription",
    "remote",
    "team",
    "focus",
}


def _normalize_region_tag(region: str) -> str | None:
    normalized = region.strip().upper().replace("-", "_")
    return normalized if normalized in REGIONS else None


def _sanitize_tone(raw: str) -> str:
    cleaned = raw.strip().lower()
    return cleaned if cleaned in ALLOWED_TONES else "thoughtful"


def collect_queue_topics(limit: int = 6) -> tuple[list[str], list[str]]:
    """Samples scheduled/queued posts to describe existing themes and regions."""
    with SessionLocal() as session:
        rows = (
            session.query(Post)
            .filter(Post.status.in_([PostStatus.queued, PostStatus.scheduled]))
            .order_by(Post.scheduled_time)
            .limit(limit)
            .all()
        )
        descriptions: list[str] = []
        regions: list[str] = []
        for post in rows:
            text = (post.text or "").strip().replace("\n", " ")
            desc = text[:120] + ("…" if len(text) > 120 else "")
            scheduled = post.scheduled_time.isoformat() if post.scheduled_time else "unscheduled"
            descriptions.append(f"{desc} (scheduled: {scheduled})")
            metadata = post.extra_metadata or {}
            for region in metadata.get("target_regions", []):
                if isinstance(region, str):
                    normalized = _normalize_region_tag(region)
                    if normalized:
                        regions.append(normalized)
        return descriptions, sorted(set(regions))


def collect_learning_profile(
    lookback_days: int | None = None,
) -> dict[str, Any]:
    lookback_days = lookback_days or _parse_int(
        os.getenv("CONTENT_LEARNING_LOOKBACK_DAYS"),
        DEFAULT_LEARNING_LOOKBACK_DAYS,
    )
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, lookback_days))
    default_profile: dict[str, Any] = {
        "commentary_ratio": DEFAULT_COMMENTARY_RATIO,
        "avg_views": 0.0,
        "reply_rate": 0.0,
        "top_tones": [],
        "underperforming_tones": [],
        "best_hours_utc": [],
        "high_performing_examples": [],
        "observations": "No recent metrics yet; use balanced style diversity.",
    }

    with SessionLocal() as session:
        posts = (
            session.query(Post)
            .filter(Post.published_time.is_not(None), Post.published_time >= cutoff)
            .all()
        )
        if not posts:
            return default_profile

        post_ids = [post.id for post in posts]
        metric_rows = (
            session.query(Metric.post_id, Metric.name, Metric.value)
            .filter(
                Metric.post_id.in_(post_ids),
                Metric.name.in_(["views", "replies", "likes", "reposts", "quotes"]),
            )
            .all()
        )

    metrics_by_post: dict[str, dict[str, float]] = {}
    for post_id, metric_name, metric_value in metric_rows:
        if not post_id:
            continue
        bucket = metrics_by_post.setdefault(post_id, {})
        previous = bucket.get(metric_name)
        value = float(metric_value or 0)
        if previous is None or value > previous:
            bucket[metric_name] = value

    scored_posts: list[dict[str, Any]] = []
    tone_scores: dict[str, list[float]] = {}
    hour_scores: dict[int, list[float]] = {}
    commentary_scores: list[float] = []
    whimsical_scores: list[float] = []
    views_values: list[float] = []
    replies_values: list[float] = []
    for post in posts:
        metrics = metrics_by_post.get(post.id, {})
        if not metrics:
            continue
        views_values.append(float(metrics.get("views", 0)))
        replies_values.append(float(metrics.get("replies", 0)))
        score = _engagement_score(metrics)
        metadata = post.extra_metadata or {}
        tone = _sanitize_tone(str(metadata.get("tone") or "thoughtful"))
        tone_scores.setdefault(tone, []).append(score)
        if post.published_time:
            hour = post.published_time.astimezone(timezone.utc).hour
            hour_scores.setdefault(hour, []).append(score)

        if tone in COMMENTARY_TONE_HINTS:
            commentary_scores.append(score)
        elif tone in WHIMSICAL_TONE_HINTS:
            whimsical_scores.append(score)

        scored_posts.append(
            {
                "tone": tone,
                "score": score,
                "text": sanitize_text(post.text, max_chars=180),
            }
        )

    if not scored_posts:
        return default_profile

    avg_by_tone = {
        tone: (sum(scores) / max(1, len(scores))) for tone, scores in tone_scores.items()
    }
    ranked_tones = sorted(avg_by_tone, key=avg_by_tone.get, reverse=True)
    low_tones = sorted(avg_by_tone, key=avg_by_tone.get)
    top_examples = sorted(scored_posts, key=lambda item: item["score"], reverse=True)[:3]
    top_hours = sorted(
        hour_scores,
        key=lambda hour: sum(hour_scores[hour]) / max(1, len(hour_scores[hour])),
        reverse=True,
    )[:3]

    commentary_avg = sum(commentary_scores) / len(commentary_scores) if commentary_scores else 0.0
    whimsical_avg = sum(whimsical_scores) / len(whimsical_scores) if whimsical_scores else 0.0
    ratio = _derive_commentary_ratio(commentary_avg, whimsical_avg)
    total_views = sum(views_values)
    reply_rate = (sum(replies_values) / total_views) if total_views > 0 else 0.0
    profile = {
        "commentary_ratio": ratio,
        "avg_views": round(_avg_numeric(views_values), 2),
        "reply_rate": round(reply_rate, 4),
        "top_tones": ranked_tones[:4],
        "underperforming_tones": low_tones[:3],
        "best_hours_utc": top_hours,
        "high_performing_examples": [entry["text"] for entry in top_examples if entry["text"]],
        "observations": _build_profile_observation(ranked_tones, top_hours),
    }
    log_insight(
        "CONTENT_LEARNING_PROFILE",
        commentary_ratio=f"{ratio:.3f}",
        reply_rate=f"{reply_rate:.4f}",
        top_tones=";".join(profile["top_tones"]),
        top_hours_utc=";".join(str(hour) for hour in top_hours),
    )
    return profile


def collect_recent_text_corpus(
    lookback_days: int | None = None,
    limit: int | None = None,
) -> list[str]:
    lookback_days = lookback_days or _parse_int(
        os.getenv("CONTENT_NOVELTY_LOOKBACK_DAYS"),
        DEFAULT_NOVELTY_LOOKBACK_DAYS,
    )
    limit = limit or _parse_int(
        os.getenv("CONTENT_NOVELTY_CORPUS_LIMIT"),
        DEFAULT_NOVELTY_CORPUS_LIMIT,
    )
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, lookback_days))
    with SessionLocal() as session:
        rows = (
            session.query(Post.text)
            .filter(
                Post.text.is_not(None),
                (
                    (Post.status.in_([PostStatus.queued, PostStatus.scheduled]) & (Post.scheduled_time >= now))
                    | (Post.published_time.is_not(None) & (Post.published_time >= cutoff))
                ),
            )
            .order_by(Post.published_time.desc(), Post.scheduled_time.desc())
            .limit(limit)
            .all()
        )
    corpus = [sanitize_text(row[0], max_chars=MAX_TEXT_LENGTH) for row in rows if row and row[0]]
    return [entry for entry in corpus if entry]


def _engagement_score(metrics: dict[str, float]) -> float:
    views = float(metrics.get("views", 0))
    replies = float(metrics.get("replies", 0))
    likes = float(metrics.get("likes", 0))
    reposts = float(metrics.get("reposts", 0))
    quotes = float(metrics.get("quotes", 0))
    return views + (replies * 220) + (quotes * 180) + (reposts * 150) + (likes * 25)


def _derive_commentary_ratio(commentary_avg: float, whimsical_avg: float) -> float:
    if commentary_avg <= 0 and whimsical_avg <= 0:
        return DEFAULT_COMMENTARY_RATIO
    if commentary_avg <= 0:
        return 0.25
    if whimsical_avg <= 0:
        return 0.55
    ratio = commentary_avg / (commentary_avg + whimsical_avg)
    return max(0.25, min(ratio, 0.55))


def _build_profile_observation(top_tones: list[str], top_hours: list[int]) -> str:
    tone_hint = ", ".join(top_tones[:3]) if top_tones else "mixed tones"
    hour_hint = ", ".join(f"{hour:02d}:00 UTC" for hour in top_hours[:2]) if top_hours else "varied hours"
    return f"Recent winners favor {tone_hint}; strong publish windows include {hour_hint}."


def _content_niche() -> str:
    raw = (os.getenv("CONTENT_NICHE", DEFAULT_CONTENT_NICHE) or "").strip().lower()
    return raw or DEFAULT_CONTENT_NICHE


def _is_ai_work_niche() -> bool:
    return _content_niche() == "ai_work_reality_check"


def _format_learning_brief(profile: dict[str, Any]) -> str:
    top_tones = ", ".join(profile.get("top_tones", [])[:4]) or "none yet"
    weak_tones = ", ".join(profile.get("underperforming_tones", [])[:3]) or "none"
    best_hours = ", ".join(str(hour) for hour in profile.get("best_hours_utc", [])[:3]) or "unknown"
    examples = profile.get("high_performing_examples", [])[:2]
    snippets = " | ".join(str(example) for example in examples) if examples else "none"
    observation = str(profile.get("observations") or "No extra observations.")
    return (
        f"top_tones={top_tones}; underperforming_tones={weak_tones}; "
        f"best_hours_utc={best_hours}; winning_examples={snippets}; note={observation}"
    )


def prompt_openai(
    entries: int,
    topics: Sequence[str],
    regions: Sequence[str],
    items: Sequence[object],
    mode: str,
    learning_profile: dict[str, Any] | None = None,
) -> Sequence[dict[str, object]]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY must be set to generate new posts via OpenAI.")
    client = OpenAI(api_key=settings.openai_api_key)
    examples = "\n".join(f"- {topic}" for topic in topics[:5]) or "no current posts"
    queue_regions = ", ".join(sorted(set(regions))) or "none yet"
    learning_brief = _format_learning_brief(learning_profile or {})
    items_payload = None
    if mode == "commentary":
        if not items:
            raise SystemExit("No RSS items available; skipping commentary generation.")
        items_payload = _format_items(
            items, limit=min(len(items), max(6, min(entries, 12)))
        )
        request = (
            f"Goal: Generate {entries} short, high-engagement Threads posts that are commentary inspired by outside sources but feel native to a home feed. "
            "Each post must be 1-2 sentences, text-only, no emojis, never include hashtags (#), no marketing language or sales phrases, and no CTAs like \"follow\" or \"link in bio\". "
            "Avoid rage bait, politics, war, famine, religion, violence, crime, or explicit sexual content, and keep the tone light. "
            "Voice: Digital Doofus, cosy wizard vibe, friendly, slightly witty, never hostile or confusing. "
            "Use the RSS items below (prefer higher edge_score). Do not invent facts or people that are not in the RSS items. "
            "Each post must stand alone for a cold reader: state what happened in plain language, name the subject explicitly in the first clause, add one concrete detail for context, and explain why it matters in everyday terms. "
            "Do not assume prior context. Avoid niche trivia unless you translate it into a universal takeaway about work, money, time, trust, habits, creativity, or online behavior. "
            "Do not reference the existence of a source, article, report, or headline; it should read like a personal observation. "
            "Each item must include a single-element target_regions array with one uppercase value from [EU, US_EAST, US_WEST]. "
            "For each post, include a source_id integer that matches the RSS item id used. Do not include URLs in the text. "
            "Tone should stay within [playful, thoughtful, curious, lightly-contrarian, motivational, whimsical, witty, absurd] and feel human and Threads-native. "
            "For each item provide three short fields we can compose into the final text: hook, payoff, conversation_prompt. "
            "hook must open with a concrete image or observation, payoff must add a surprising detail or implication, conversation_prompt must invite a reaction without saying follow/subscribe. "
            f"Current queue themes: {examples}. Regional queue history: {queue_regions}. "
            f"Learning profile from recent performance: {learning_brief}. "
            f"RSS items (JSON): {items_payload}. "
            "Return only a JSON array following {\"topic\":..., \"tone\":..., \"hook\":..., \"payoff\":..., \"conversation_prompt\":..., \"text\":..., \"target_regions\":[...], \"source_id\": ...} with double quotes for every string and no additional explanation."
        )
        if _is_ai_work_niche():
            request += (
                " Niche lock: AI Work Reality Check only. "
                "Every post must connect AI/automation to real work behavior (inbox, meetings, creator workflow, support, team collaboration, trust, speed vs quality). "
                "Avoid novelty trivia and avoid broad generic life advice."
            )
    elif mode == "whimsical":
        request = (
            f"Goal: Generate {entries} short, high-engagement Threads posts that are witty, relatable, and rooted in everyday digital life. "
            "Each post must be 1-2 short sentences, text-only, no emojis, never include hashtags (#), no marketing language or sales phrases, and no CTAs like \"follow\" or \"link in bio\". "
            "Avoid rage bait, politics, war, famine, religion, violence, crime, or explicit sexual content, and keep the tone light. "
            "Voice: Digital Doofus, cosy wizard vibe, friendly, slightly witty, never hostile or confusing. "
            "Do not reference or imply any outside source or news item. Do not include URLs. "
            "Make each post understandable with zero prior context. Keep scenarios relatable to normal life, work, internet behavior, or creativity. "
            "Every post must include one concrete modern context (for example: inbox overload, group chats, notifications, remote meetings, creator workflow, AI assistants, subscriptions, calendar chaos). "
            "Avoid abstract nonsense and cliches like devices having a secret life, talking trees, or being judged by your phone. "
            "Each item must include a single-element target_regions array with one uppercase value from [EU, US_EAST, US_WEST]. "
            "Tone should stay within [playful, thoughtful, curious, lightly-contrarian, motivational, whimsical, witty, absurd] and feel human and Threads-native. "
            "For each item provide three short fields we can compose into the final text: hook, payoff, conversation_prompt. "
            "hook should be vivid, payoff should add a practical twist, conversation_prompt should be a specific natural reply magnet. "
            f"Current queue themes: {examples}. Regional queue history: {queue_regions}. "
            f"Learning profile from recent performance: {learning_brief}. "
            "Return only a JSON array following {\"topic\":..., \"tone\":..., \"hook\":..., \"payoff\":..., \"conversation_prompt\":..., \"text\":..., \"target_regions\":[...]} with double quotes for every string and no additional explanation."
        )
        if _is_ai_work_niche():
            request += (
                " Niche lock: keep all posts inside AI Work Reality Check. "
                "Humor is allowed, but each post must still reference practical digital work context."
            )
    else:
        raise SystemExit(f"Unknown prompt mode: {mode}")
    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {
                "role": "system",
                "content": "You are the Threads content planning assistant. Your job is to output tidy JSON without any text outside of the JSON.",
            },
            {"role": "user", "content": request},
        ],
        temperature=0.7,
        max_tokens=min(2000, 120 * entries + 300),
    )
    _record_openai_usage(response, settings.openai_token_cost_per_1k)
    raw = response.choices[0].message.content
    return _parse_openai_response(raw)


def _parse_openai_response(raw: str) -> Sequence[dict[str, object]]:
    stripped = raw.strip()
    parsed = _try_json_load(stripped)
    if parsed is None:
        openai_logger.warning("OpenAI raw response (truncated): %s", _trim_text(stripped, 4000))
        candidate = _extract_json_candidate(stripped)
        parsed = _try_json_load(candidate)
        if parsed is None:
            parsed = _try_literal_eval(candidate)
            if parsed is None:
                parsed = _salvage_json_array(candidate)
            if parsed is None:
                logger.warning("Unable to parse OpenAI response; last candidate: %s", candidate)
                raise SystemExit("Unable to parse OpenAI response as JSON; check logs for the raw response.")
    return _normalize_entries(parsed)


def _normalize_entries(parsed: object) -> Sequence[dict[str, object]]:
    if isinstance(parsed, dict) and isinstance(parsed.get("posts"), list):
        return parsed["posts"]
    if isinstance(parsed, list):
        return parsed
    raise SystemExit("OpenAI response did not contain a JSON array of posts.")


def _get_source_id(entry: dict[str, object], items_len: int) -> int | None:
    source_id = entry.get("source_id")
    if isinstance(source_id, str) and source_id.isdigit():
        source_id = int(source_id)
    if not isinstance(source_id, int):
        return None
    if source_id < 0 or source_id >= items_len:
        return None
    return source_id


def _contains_url(text: str) -> bool:
    lowered = text.lower()
    return "http://" in lowered or "https://" in lowered or "www." in lowered


def _extract_json_candidate(raw: str) -> str:
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _try_json_load(payload: str) -> object | None:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        repaired = re.sub(r",(\s*[}\]])", r"\1", payload)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None


def _try_literal_eval(payload: str) -> object | None:
    cleaned = payload
    cleaned = re.sub(r"\bnull\b", "None", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\btrue\b", "True", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfalse\b", "False", cleaned, flags=re.IGNORECASE)
    try:
        return ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        return None


def _salvage_json_array(payload: str) -> list[dict[str, object]] | None:
    start = payload.find("[")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    idx = start + 1
    items: list[dict[str, object]] = []
    while idx < len(payload):
        while idx < len(payload) and payload[idx] in " \r\n\t,":
            idx += 1
        if idx >= len(payload) or payload[idx] == "]":
            break
        try:
            item, end = decoder.raw_decode(payload, idx)
        except json.JSONDecodeError:
            break
        if isinstance(item, dict):
            items.append(item)
        idx = end
    return items or None


def _trim_text(value: str, limit: int) -> str:
    cleaned = " ".join((value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _format_items(items: Sequence[object], limit: int) -> str:
    payload: list[dict[str, object]] = []
    for idx, item in enumerate(list(items)[:limit]):
        title = _trim_text(getattr(item, "title", ""), 120)
        summary = _trim_text(getattr(item, "summary", ""), 180)
        source = getattr(item, "source", "")
        edge_score = getattr(item, "edge_score", 0)
        payload.append(
            {
                "id": idx,
                "title": title,
                "summary": summary,
                "source": source,
                "edge_score": edge_score,
            }
        )
    return json.dumps(payload, ensure_ascii=True)


def _extract_candidate_text(entry: dict[str, object]) -> str:
    direct = sanitize_text(str(entry.get("text", "")).strip(), max_chars=MAX_TEXT_LENGTH)
    hook = _line_fragment(entry.get("hook"))
    payoff = _line_fragment(entry.get("payoff"))
    prompt = _line_fragment(entry.get("conversation_prompt"))
    composed = _compose_fragments(hook, payoff, prompt)
    if direct and len(direct) >= 40:
        return direct
    if composed:
        return composed
    return direct


def _line_fragment(value: object) -> str:
    text = sanitize_text(str(value or "").strip(), max_chars=220)
    if not text:
        return ""
    return _ensure_sentence_end(text)


def _compose_fragments(hook: str, payoff: str, conversation_prompt: str) -> str:
    parts = [part for part in [hook, payoff, conversation_prompt] if part]
    if not parts:
        return ""
    text = " ".join(parts)
    return sanitize_text(text, max_chars=MAX_TEXT_LENGTH)


def _min_specific_prompt_ratio(learning_profile: dict[str, Any]) -> float:
    base = _parse_float(
        os.getenv("CONTENT_MIN_SPECIFIC_PROMPT_RATIO"),
        DEFAULT_MIN_SPECIFIC_PROMPT_RATIO,
    )
    ratio = max(0.4, min(base, 0.95))
    reply_rate = float(learning_profile.get("reply_rate", 0) or 0)
    if reply_rate < LOW_REPLY_RATE_THRESHOLD:
        ratio = max(ratio, LOW_REPLY_SPECIFIC_PROMPT_RATIO)
    return ratio


def _extract_terminal_question(text: str) -> str:
    matches = re.findall(r"[^.!?]*\?", text)
    if not matches:
        return ""
    return matches[-1].strip()


def _is_generic_question(question: str) -> bool:
    normalized = re.sub(r"[^a-z0-9'\s]", "", question.lower()).strip()
    if not normalized:
        return True
    if normalized in GENERIC_QUESTION_STRINGS:
        return True
    words = normalized.split()
    if len(words) <= 4 and any(token in normalized for token in ("thought", "take", "agree")):
        return True
    return False


def _is_specific_question_ending(text: str) -> bool:
    question = _extract_terminal_question(text)
    if not question:
        return False
    if _is_generic_question(question):
        return False
    lowered = question.lower()
    words = re.findall(r"[a-z0-9']+", lowered)
    if len(words) < 6:
        return False
    if "you" not in words and "your" not in words:
        return False
    if not any(
        marker in words
        for marker in ("which", "what", "how", "where", "when", "who", "would", "could", "should", "do")
    ):
        return False
    if lowered.startswith("which "):
        return True
    return any(term in lowered for term in SPECIFIC_QUESTION_SIGNAL_TERMS)


def _specific_question_for_topic(topic: str) -> str:
    topic_hint = sanitize_text(topic, max_chars=60).strip(" .,!?:;") or "this trend"
    use_topic_template = len(topic_hint.split()) <= 5 and random.random() < 0.35
    if use_topic_template:
        template = random.choice(SPECIFIC_PROMPT_TEMPLATES_TOPIC)
        return template.format(topic=topic_hint)
    return random.choice(SPECIFIC_PROMPT_TEMPLATES_GENERIC)


def _strip_terminal_question(text: str) -> str:
    trimmed = re.sub(r"\s*[^.!?]*\?\s*$", "", text).strip()
    if not trimmed:
        return ""
    return _ensure_sentence_end(trimmed)


def _upgrade_with_specific_prompt(entry: dict[str, object]) -> str | None:
    original = sanitize_text(str(entry.get("text", "")).strip(), max_chars=MAX_TEXT_LENGTH)
    if not original:
        return None
    base = _strip_terminal_question(original) if original.rstrip().endswith("?") else _ensure_sentence_end(original)
    topic = sanitize_text(str(entry.get("topic", "")).strip(), max_chars=80) or "this"
    specific_prompt = _specific_question_for_topic(topic)
    candidate = f"{base} {specific_prompt}".strip()
    if len(candidate) > MAX_TEXT_LENGTH:
        allowed_base = MAX_TEXT_LENGTH - len(specific_prompt) - 1
        if allowed_base < 30:
            return None
        clipped_base = sanitize_text(base, max_chars=allowed_base).rstrip(" ,;:-")
        if not clipped_base:
            return None
        candidate = f"{_ensure_sentence_end(clipped_base)} {specific_prompt}"
    candidate = sanitize_text(candidate, max_chars=MAX_TEXT_LENGTH)
    return candidate if _is_specific_question_ending(candidate) else None


def _enforce_specific_prompt_ratio(
    entries: list[dict[str, object]],
    learning_profile: dict[str, Any],
) -> int:
    if not entries:
        return 0
    target_ratio = _min_specific_prompt_ratio(learning_profile)
    required = math.ceil(len(entries) * target_ratio)
    specific_indexes = [
        idx for idx, entry in enumerate(entries) if _is_specific_question_ending(str(entry.get("text", "")))
    ]
    if len(specific_indexes) >= required:
        log_insight(
            "CONTENT_PROMPT_RATIO",
            total=len(entries),
            required=required,
            specific=len(specific_indexes),
            target_ratio=f"{target_ratio:.2f}",
            achieved_ratio=f"{len(specific_indexes) / len(entries):.2f}",
            rewritten=0,
        )
        return 0

    rewritten = 0
    candidates = [idx for idx in range(len(entries)) if idx not in specific_indexes]
    random.shuffle(candidates)
    for idx in candidates:
        if len(specific_indexes) >= required:
            break
        rewritten_text = _upgrade_with_specific_prompt(entries[idx])
        if not rewritten_text:
            continue
        entries[idx]["text"] = rewritten_text
        specific_indexes.append(idx)
        rewritten += 1

    achieved_ratio = (len(specific_indexes) / len(entries)) if entries else 0.0
    log_insight(
        "CONTENT_PROMPT_RATIO",
        total=len(entries),
        required=required,
        specific=len(specific_indexes),
        target_ratio=f"{target_ratio:.2f}",
        achieved_ratio=f"{achieved_ratio:.2f}",
        rewritten=rewritten,
    )
    return rewritten


def _ensure_sentence_end(text: str) -> str:
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    return f"{text}."


def _normalize_entry_regions(entry: dict[str, object]) -> list[str]:
    value = entry.get("target_regions")
    if isinstance(value, list):
        regions = [str(region).strip() for region in value if str(region).strip()]
    elif isinstance(value, str):
        regions = [segment.strip() for segment in value.replace(";", ",").split(",") if segment.strip()]
    else:
        regions = []
    normalized: list[str] = []
    for region in regions:
        normalized_region = _normalize_region_tag(region)
        if normalized_region and normalized_region not in normalized:
            normalized.append(normalized_region)
    return normalized


def _is_novel_enough(text: str, corpus: Sequence[str], threshold: float) -> bool:
    if not corpus:
        return True
    score = max_similarity(text, corpus)
    return score < threshold


def _fallback_batch_text(topic: str, tone: str, post_type: str) -> str:
    topic_clean = sanitize_text(topic, max_chars=80) or "this idea"
    tone_clean = sanitize_text(tone, max_chars=30) or "thoughtful"
    if post_type == "commentary":
        return (
            f"{topic_clean} sounds niche at first, but it usually points to a bigger shift in how people work and decide. "
            f"From your {tone_clean} perspective, what is one practical takeaway you would test this week?"
        )
    return (
        f"{topic_clean} usually feels small, but it changes where attention goes in everyday digital routines. "
        f"What is one practical experiment you would try for a week?"
    )


def _topic_overlap_tokens(topic: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9']+", topic or "")
        if len(token) >= 4 and token.lower() not in TOPIC_STOPWORDS
    }
    return tokens


def _clarity_issues(text: str, topic: str, mode: str) -> list[str]:
    issues: list[str] = []
    cleaned = sanitize_text(text or "", max_chars=MAX_TEXT_LENGTH)
    lowered = cleaned.lower()
    words = re.findall(r"[a-z0-9']+", lowered)
    if words and words[0] in CONTEXTLESS_OPENERS:
        issues.append("contextless_opening")
    if words and words[0] in {"what", "which", "how", "would", "do", "should"}:
        issues.append("question_only_opening")
    if mode == "commentary" and _count_sentences(cleaned) < 2:
        issues.append("missing_setup_sentence")
    if any(phrase in lowered for phrase in GENERIC_FILLER_PHRASES):
        issues.append("generic_filler")
    if any(term in lowered for term in NICHE_TOPIC_TERMS):
        issues.append("niche_term")
    if any(phrase in lowered for phrase in CLICHE_PHRASES):
        issues.append("cliche_phrase")
    if mode == "whimsical" and not any(term in lowered for term in RELATABLE_CONTEXT_TERMS):
        issues.append("missing_relatable_context")
    if _is_ai_work_niche() and not any(term in lowered for term in AI_WORK_NICHE_TERMS):
        issues.append("missing_ai_work_context")
    topic_tokens = _topic_overlap_tokens(topic)
    if mode == "commentary" and topic_tokens:
        if not any(token in lowered for token in topic_tokens):
            issues.append("topic_not_explicit")
    return issues


def _generate_entries(
    count: int,
    mode: str,
    topics: Sequence[str],
    regions: Sequence[str],
    items: Sequence[object],
    learning_profile: dict[str, Any],
    baseline_corpus: Sequence[str],
) -> list[dict[str, object]]:
    if count <= 0:
        return []
    entries: list[dict[str, object]] = []
    attempts = 0
    rejected = 0
    rewritten = 0
    oversample_factor = _adaptive_oversample_factor(learning_profile)
    rewrite_budget = _rewrite_budget(count)
    novelty_threshold = _adaptive_novelty_threshold(learning_profile)
    corpus = list(baseline_corpus)
    min_chars = 45 if mode == "commentary" else 35
    while len(entries) < count and attempts < 5:
        attempts += 1
        remaining = count - len(entries)
        requested = max(remaining, remaining * oversample_factor)
        requested = min(requested, max(remaining, count * 3))
        try:
            batch = list(
                prompt_openai(
                    requested,
                    topics,
                    regions,
                    items,
                    mode,
                    learning_profile=learning_profile,
                )
            )
        except SystemExit as exc:
            logger.warning("OpenAI %s generation attempt %s failed: %s", mode, attempts, exc)
            continue

        for raw_entry in batch:
            if len(entries) >= count:
                break
            if not isinstance(raw_entry, dict):
                rejected += 1
                continue
            entry = dict(raw_entry)
            entry["tone"] = _sanitize_tone(str(entry.get("tone", "")))
            entry["target_regions"] = _normalize_entry_regions(entry) or [random.choice(REGIONS)]
            text = _extract_candidate_text(entry)
            if not text or _contains_url(text):
                rejected += 1
                continue
            entry["text"] = text

            if mode == "commentary":
                source_id = _get_source_id(entry, len(items))
                if source_id is None:
                    rejected += 1
                    continue
                entry["source_id"] = source_id
                entry["post_type"] = "commentary"
            else:
                entry.pop("source_id", None)
                entry["post_type"] = "whimsical"

            reasons = quality_issues(
                entry["text"],
                min_chars=min_chars,
                max_chars=MAX_TEXT_LENGTH,
                forbid_url=True,
            )
            reasons.extend(_clarity_issues(entry["text"], str(entry.get("topic", "")), mode))
            if reasons and _is_ai_work_niche() and mode == "commentary":
                locally_rewritten = _rewrite_ai_work_entry_locally(entry)
                if locally_rewritten:
                    entry["text"] = locally_rewritten
                    reasons = quality_issues(
                        entry["text"],
                        min_chars=min_chars,
                        max_chars=MAX_TEXT_LENGTH,
                        forbid_url=True,
                    )
                    reasons.extend(_clarity_issues(entry["text"], str(entry.get("topic", "")), mode))
            if reasons and rewrite_budget > 0:
                rewritten_text = _rewrite_entry_text(
                    entry,
                    mode=mode,
                    reasons=reasons,
                    learning_profile=learning_profile,
                )
                rewrite_budget -= 1
                if rewritten_text:
                    entry["text"] = rewritten_text
                    rewritten += 1
                    reasons = quality_issues(
                        entry["text"],
                        min_chars=min_chars,
                        max_chars=MAX_TEXT_LENGTH,
                        forbid_url=True,
                    )
                    reasons.extend(_clarity_issues(entry["text"], str(entry.get("topic", "")), mode))

            if reasons:
                rejected += 1
                continue
            if not _is_novel_enough(entry["text"], corpus, novelty_threshold):
                rejected += 1
                continue
            entries.append(entry)
            corpus.append(entry["text"])

    log_insight(
        "CONTENT_FILTER_RESULTS",
        mode=mode,
        requested=count,
        accepted=len(entries),
        rejected=rejected,
        rewritten=rewritten,
        oversample_factor=oversample_factor,
    )
    return entries[:count]


def _interleave_entries(
    commentary: list[dict[str, object]],
    whimsical: list[dict[str, object]],
) -> list[dict[str, object]]:
    random.shuffle(commentary)
    random.shuffle(whimsical)
    result: list[dict[str, object]] = []
    c_idx = 0
    w_idx = 0
    while c_idx < len(commentary) or w_idx < len(whimsical):
        if c_idx < len(commentary):
            result.append(commentary[c_idx])
            c_idx += 1
        for _ in range(2):
            if w_idx < len(whimsical):
                result.append(whimsical[w_idx])
                w_idx += 1
    return result


def _rewrite_entry_text(
    entry: dict[str, object],
    *,
    mode: str,
    reasons: Sequence[str],
    learning_profile: dict[str, Any],
) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    source_text = sanitize_text(str(entry.get("text", "")), max_chars=MAX_TEXT_LENGTH)
    if not source_text:
        return None
    client = OpenAI(api_key=settings.openai_api_key)
    learning_brief = _format_learning_brief(learning_profile)
    reason_text = ", ".join(reasons)
    rewrite_request = (
        "Rewrite the Threads draft below so it is high-quality and compliant. "
        "Keep the same core idea, but improve specificity and engagement. "
        "Make it self-contained for a cold reader and explicitly name the topic early. "
        "Avoid niche trivia unless connected to a universal takeaway. "
        "Return only JSON: {\"text\": \"...\"}. "
        f"Mode: {mode}. Reasons to fix: {reason_text}. Learning profile: {learning_brief}. "
        "Hard constraints: 1-3 short sentences, no hashtags, no emojis, no URLs, no marketing CTA, no politics/war/religion/violence/crime/sexual content."
    )
    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": "You rewrite social copy and return clean JSON only."},
            {
                "role": "user",
                "content": f"{rewrite_request}\nDraft: {source_text}",
            },
        ],
        max_tokens=180,
        temperature=0.55,
    )
    _record_openai_usage(response, settings.openai_token_cost_per_1k)
    raw = response.choices[0].message.content or ""
    parsed = _try_json_load(raw.strip())
    if not isinstance(parsed, dict):
        parsed = _try_json_load(_extract_json_candidate(raw.strip()))
    if not isinstance(parsed, dict):
        return None
    rewritten = sanitize_text(str(parsed.get("text", "")).strip(), max_chars=MAX_TEXT_LENGTH)
    return rewritten or None


def _rewrite_ai_work_entry_locally(entry: dict[str, object]) -> str | None:
    topic = sanitize_text(str(entry.get("topic", "")).strip(), max_chars=70)
    if not topic:
        topic = "AI at work"
    setup = random.choice(AI_WORK_SETUP_TEMPLATES).format(topic=topic)
    question = random.choice(AI_WORK_QUESTION_TEMPLATES)
    candidate = sanitize_text(f"{setup} {question}", max_chars=MAX_TEXT_LENGTH)
    issues = quality_issues(
        candidate,
        min_chars=45,
        max_chars=MAX_TEXT_LENGTH,
        forbid_url=True,
    )
    issues.extend(_clarity_issues(candidate, topic, "commentary"))
    if issues:
        return None
    return candidate


def _oversample_factor() -> int:
    return max(
        1,
        _parse_int(
            os.getenv("CONTENT_OVERSAMPLE_FACTOR"),
            DEFAULT_OVERSAMPLE_FACTOR,
        ),
    )


def _rewrite_budget(expected_count: int) -> int:
    default_budget = max(DEFAULT_REWRITE_BUDGET, expected_count // 2)
    return max(0, _parse_int(os.getenv("CONTENT_REWRITE_BUDGET"), default_budget))


def _novelty_threshold() -> float:
    raw = os.getenv("CONTENT_NOVELTY_THRESHOLD")
    if raw is None:
        return SIMILARITY_THRESHOLD_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return SIMILARITY_THRESHOLD_DEFAULT
    return max(0.4, min(value, 0.95))


def _adaptive_oversample_factor(learning_profile: dict[str, Any]) -> int:
    base = _oversample_factor()
    avg_views = float(learning_profile.get("avg_views", 0) or 0)
    if avg_views and avg_views < 10:
        base = max(base, 3)
    if _is_ai_work_niche():
        base = max(base, 4)
    return base


def _adaptive_novelty_threshold(learning_profile: dict[str, Any]) -> float:
    base = _novelty_threshold()
    avg_views = float(learning_profile.get("avg_views", 0) or 0)
    if avg_views and avg_views < 10:
        return min(base, 0.68)
    return base


def generate_predefined_posts(
    days: int,
    per_day: int,
    force: bool = False,
    csv_path: Path | str | None = None,
) -> int:
    per_day = max(1, per_day)
    if per_day > 6:
        logger.info("per-day cap is 6; clamping requested value %s to 6.", per_day)
        per_day = 6
    expected_rows = days * per_day
    if expected_rows == 0:
        raise ValueError("Choose --days/--per-day such that at least one post is requested.")
    if expected_rows % len(REGIONS) != 0:
        raise ValueError("Total posts must be divisible by 3 so each region receives an equal batch.")

    target_path = Path(csv_path or CSV_PATH)

    if not force and target_path.exists():
        existing_rows = sum(1 for _ in csv.DictReader(target_path.open()))
        if existing_rows >= expected_rows:
            logger.info(
                "CSV already has %d rows; target is %d, so skipping rewrite.",
                existing_rows,
                expected_rows,
            )
            return 0

    topics, regions = collect_queue_topics()
    learning_profile = collect_learning_profile()
    baseline_corpus = collect_recent_text_corpus()
    items = fetch_edge_items(limit=max(12, expected_rows))
    adaptive_ratio = float(learning_profile.get("commentary_ratio", DEFAULT_COMMENTARY_RATIO))
    if _is_ai_work_niche():
        adaptive_ratio = 1.0
    else:
        adaptive_ratio = max(0.6, min(adaptive_ratio, 0.8))
    commentary_count = int(round(expected_rows * adaptive_ratio))
    if adaptive_ratio >= 0.999:
        commentary_count = expected_rows
    else:
        commentary_count = max(1, min(expected_rows - 1, commentary_count))
    whimsical_count = expected_rows - commentary_count
    commentary_entries = _generate_entries(
        commentary_count,
        "commentary",
        topics,
        regions,
        items,
        learning_profile,
        baseline_corpus,
    )
    working_corpus = baseline_corpus + [entry.get("text", "") for entry in commentary_entries]
    whimsical_entries = _generate_entries(
        whimsical_count,
        "whimsical",
        topics,
        regions,
        items,
        learning_profile,
        working_corpus,
    )
    if len(commentary_entries) < commentary_count or len(whimsical_entries) < whimsical_count:
        raise SystemExit(
            "OpenAI returned too few posts to meet the adaptive commentary/whimsical mix; "
            f"got {len(commentary_entries)} commentary and {len(whimsical_entries)} whimsical."
        )
    entries = _interleave_entries(commentary_entries, whimsical_entries)
    raw_ratio = os.getenv("PREDEFINED_POSTS_TINYURL_RATIO")
    allow_links = os.getenv("CONTENT_ALLOW_SOURCE_LINKS", "false").lower() in {"1", "true", "yes"}
    if raw_ratio is None:
        link_target = 0
    else:
        link_target = int(round(expected_rows * _parse_ratio(raw_ratio)))
    if allow_links and link_target > 0:
        _append_tinyurls(entries, items, target_count=link_target)
    elif link_target > 0:
        logger.info(
            "Skipping link injection because CONTENT_ALLOW_SOURCE_LINKS is disabled."
        )
    prompt_rewrites = _enforce_specific_prompt_ratio(entries, learning_profile)

    log_insight(
        "CONTENT_BATCH_MIX",
        expected_rows=expected_rows,
        commentary_count=commentary_count,
        whimsical_count=whimsical_count,
        adaptive_ratio=f"{adaptive_ratio:.3f}",
        prompt_rewrites=prompt_rewrites,
    )
    scheduled = schedule_posts(entries, days, per_day)
    rewrite_csv(scheduled, target_path)
    return len(scheduled)


_MIN_DELTA = timedelta(seconds=1)
_MIN_REGION_GAP = timedelta(minutes=30)


def _random_time_for_region(
    region: str,
    preferred_date: date,
    earliest: datetime,
    window_index: int = 0,
) -> datetime:
    search_date = preferred_date
    limit = 30
    candidate_earliest = earliest
    candidate_window_index = window_index % max(1, len(REGIONAL_WINDOWS.get(region, [])))
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
    raise SystemExit("Could not find a future slot for predefined posts within 30 days.")


def _random_time_for_date(
    region: str,
    target_date: date,
    earliest_for_date: datetime | None,
    window_index: int = 0,
) -> datetime | None:
    windows = REGIONAL_WINDOWS[region]
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
            tzinfo=timezone.utc,
        )
        end_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            end_min // 60,
            end_min % 60,
            tzinfo=timezone.utc,
        )
        if earliest_for_date and end_dt <= earliest_for_date:
            continue
        allowed_start = start_dt
        if earliest_for_date and earliest_for_date > start_dt:
            allowed_start = earliest_for_date + _MIN_DELTA
        if allowed_start >= end_dt:
            continue
        available_seconds = int((end_dt - allowed_start).total_seconds())
        offset = random.randint(0, available_seconds - 1) if available_seconds > 1 else 0
        return allowed_start + timedelta(seconds=offset)
    return None


def _record_openai_usage(response: object, cost_per_1k: float) -> None:
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    estimated_cost = None
    if total_tokens is not None:
        estimated_cost = total_tokens / 1000 * cost_per_1k
    log_insight(
        "OPENAI_USAGE",
        model="gpt-4.1-nano",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost=f"{estimated_cost:.6f}" if estimated_cost is not None else None,
    )
    openai_logger.info(
        "Model=gpt-4.1-nano prompt_tokens=%s completion_tokens=%s total_tokens=%s cost=%s",
        prompt_tokens,
        completion_tokens,
        total_tokens,
        f"{estimated_cost:.6f}" if estimated_cost is not None else "0.000000",
    )


def schedule_posts(
    entries: Sequence[dict[str, object]], days: int, per_day: int
) -> Sequence[dict[str, str]]:
    """Assign scheduled_time in UTC following the regional windows."""
    total = min(len(entries), days * per_day)
    if total == 0:
        return []
    if total % len(REGIONS) != 0:
        raise SystemExit(
            "Total posts must be divisible by 3 so they can be distributed evenly across EU/US_EAST/US_WEST."
        )
    entries = list(entries)[:total]
    region_sequence = [REGIONS[i % len(REGIONS)] for i in range(total)]
    dates = [
        (datetime.now(timezone.utc) + timedelta(days=i)).date() for i in range(days)
    ]
    scheduled: list[dict[str, str]] = []
    idx = 0
    now = datetime.now(timezone.utc)
    last_scheduled = {region: now - _MIN_REGION_GAP for region in REGIONS}
    window_cursor: dict[tuple[str, date], int] = {}
    for current_date in dates:
        for _ in range(per_day):
            if idx >= total:
                break
            entry = entries[idx]
            region = region_sequence[idx]
            earliest = max(now, last_scheduled.get(region, now) + _MIN_REGION_GAP)
            cursor_key = (region, current_date)
            window_index = window_cursor.get(cursor_key, 0)
            scheduled_time = _random_time_for_region(
                region,
                current_date,
                earliest,
                window_index=window_index,
            )
            scheduled.append(
                {
                    "topic": str(entry.get("topic", "")).strip(),
                    "tone": _sanitize_tone(str(entry.get("tone", ""))),
                    "text": _scheduled_text(entry),
                    "media_type": "text",
                    "media_url": "",
                    "scheduled_time": scheduled_time.isoformat(),
                    "target_regions": region,
                }
            )
            idx += 1
            last_scheduled[region] = scheduled_time
            scheduled_key = (region, scheduled_time.date())
            window_cursor[scheduled_key] = window_cursor.get(scheduled_key, 0) + 1
        if idx >= total:
            break
    return scheduled


def _scheduled_text(entry: dict[str, object]) -> str:
    topic = str(entry.get("topic", "")).strip()
    tone = _sanitize_tone(str(entry.get("tone", "")))
    post_type = str(entry.get("post_type", "whimsical"))
    text = sanitize_text(str(entry.get("text", "")).strip(), max_chars=MAX_TEXT_LENGTH)
    issues = quality_issues(text, min_chars=35, max_chars=MAX_TEXT_LENGTH, forbid_url=True)
    if issues:
        logger.warning("Replacing weak generated text during scheduling: reasons=%s", ",".join(issues))
        return _fallback_batch_text(topic, tone, post_type)
    return text


def rewrite_csv(rows: Sequence[dict[str, str]], csv_path: Path) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _append_tinyurls(
    entries: list[dict[str, object]],
    items: Sequence[object],
    target_count: int | None = None,
) -> None:
    if not entries or not items:
        return
    if target_count is None:
        raw_ratio = os.getenv("PREDEFINED_POSTS_TINYURL_RATIO")
        if raw_ratio is None:
            target_count = max(1, len(entries) // 3)
        else:
            ratio = _parse_ratio(raw_ratio)
            target_count = int(round(len(entries) * ratio))
    if target_count <= 0:
        return
    candidates: list[tuple[dict[str, object], str]] = []
    for entry in entries:
        if entry.get("post_type") != "commentary":
            continue
        source_id = _get_source_id(entry, len(items))
        if source_id is None:
            continue
        link = getattr(items[source_id], "link", "")
        if link:
            candidates.append((entry, link))
    if not candidates:
        return
    target_count = min(target_count, len(candidates))
    random.shuffle(candidates)
    cache: dict[str, str | None] = {}
    attached = 0
    for entry, link in candidates:
        if attached >= target_count:
            break
        text = str(entry.get("text", "")).strip()
        if not text or "http" in text.lower():
            continue
        if _count_sentences(text) >= 3:
            text = _truncate_sentences(text, 2)
            if _count_sentences(text) >= 3:
                continue
        short_url = cache.get(link)
        if short_url is None:
            short_url = _shorten_url(link)
            cache[link] = short_url
        if not short_url:
            continue
        updated = f"{text} Source: {short_url}."
        if len(updated) > MAX_TEXT_LENGTH:
            continue
        entry["text"] = updated
        attached += 1
    if attached < target_count:
        logger.warning(
            "Only attached %d TinyURL links out of requested %d; review commentary posts.",
            attached,
            target_count,
        )


def _shorten_url(url: str) -> str | None:
    try:
        response = httpx.get(
            TINYURL_ENDPOINT,
            params={"url": url},
            timeout=5.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("TinyURL shorten failed for %s: %s", url, exc)
        return None
    shortened = response.text.strip()
    if not shortened.startswith("http"):
        logger.warning("TinyURL returned unexpected payload: %s", shortened)
        return None
    return shortened


def _count_sentences(text: str) -> int:
    return len(re.findall(r"[.!?]", text))


def _truncate_sentences(text: str, max_sentences: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    trimmed = " ".join(parts[:max_sentences]).strip()
    return trimmed


def _parse_ratio(raw: str) -> float:
    try:
        return max(0.0, min(float(raw), 1.0))
    except (TypeError, ValueError):
        return TINYURL_RATIO


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: str | None, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg_numeric(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate predefined posts via OpenAI.")
    parser.add_argument(
        "--days", type=int, default=3, help="Number of upcoming days to fill."
    )
    parser.add_argument(
        "--per-day",
        type=int,
        default=len(REGIONS),
        help="Posts per day to schedule.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite CSV regardless of row count.")
    args = parser.parse_args()

    try:
        written = generate_predefined_posts(args.days, args.per_day, args.force)
    except ValueError as exc:
        raise SystemExit(exc)

    if written:
        print(f"Wrote {written} entries to {CSV_PATH}.")
    else:
        print("Predefined posts CSV already met the requested count; no changes made.")


if __name__ == "__main__":
    main()
