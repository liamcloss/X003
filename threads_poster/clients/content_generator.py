from __future__ import annotations

import logging
import os
from typing import Any

from threads_poster.core.config import get_settings
from threads_poster.services.content_quality import quality_issues, sanitize_text
from threads_poster.services.predefined_posts import PredefinedPostRepository

logger = logging.getLogger(__name__)


class ContentGenerator:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._repo = PredefinedPostRepository(self._settings.predefined_posts_csv)
        self._allow_llm_for_queue = self._settings.allow_llm_for_queue
        self._queue_llm_model = os.getenv("QUEUE_LLM_MODEL", "gpt-4.1-nano")
        self._queue_llm_generate_images = (
            os.getenv("QUEUE_LLM_GENERATE_IMAGES", "false").lower() in {"1", "true", "yes"}
        )

    def generate_post(self, topic: str, tone: str) -> dict[str, Any]:
        if self._allow_llm_for_queue:
            return self._generate_with_llm(topic, tone)

        return self._generate_from_predefined(topic, tone)

    def _generate_with_llm(self, topic: str, tone: str) -> dict[str, Any]:
        if not self._settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for LLM content generation")

        from openai import OpenAI

        client = OpenAI(api_key=self._settings.openai_api_key)
        prompt = (
            "Create one concise text-only Threads post about the given topic. "
            "Keep it under 500 characters, 1-3 short sentences, no hashtags, no emojis, and no marketing CTA."
        )
        chat_response = client.chat.completions.create(
            model=self._queue_llm_model,
            messages=[
                {"role": "system", "content": "You are a social media copywriter."},
                {"role": "user", "content": f"Topic: {topic}\nTone: {tone}\n{prompt}"},
            ],
            max_tokens=180,
            temperature=0.7,
        )
        text = sanitize_text(chat_response.choices[0].message.content, max_chars=500)
        text = self._quality_guard_text(client, topic, tone, text)

        media_url = None
        media_type = "TEXT"
        if self._queue_llm_generate_images:
            image_prompt = (
                "Create a vibrant, engaging photo-style image that matches this Threads post: "
                f"{text}"
            )
            try:
                image_response = client.images.generate(
                    model="gpt-image-1",
                    prompt=image_prompt,
                    size="1024x1024",
                )
                media_url = image_response.data[0].url
                media_type = "IMAGE"
            except Exception:
                logger.exception("Failed to generate image, falling back to text-only post")
                media_type = "TEXT"

        return {
            "text": text,
            "media_type": media_type,
            "media_url": media_url,
        }

    def _generate_from_predefined(self, topic: str, tone: str) -> dict[str, Any]:
        post = self._repo.find(topic, tone)
        if not post:
            raise ValueError(
                "No predefined posts available; enable ALLOW_LLM_FOR_QUEUE=true or add entries to the CSV."
            )

        payload = post.to_payload()
        return {
            "text": payload["text"],
            "media_type": payload["media_type"],
            "media_url": payload["media_url"],
            "scheduled_time": payload["scheduled_time"],
            "target_regions": payload["target_regions"],
        }

    def _quality_guard_text(self, client, topic: str, tone: str, text: str) -> str:
        issues = quality_issues(text, min_chars=35, max_chars=500, forbid_url=True)
        if not issues:
            return text

        rewrite_prompt = (
            "Rewrite this Threads draft so it is concise and engaging. "
            "Keep the core idea, use 1-3 short sentences, no hashtags, no emojis, no URLs, and no marketing CTA. "
            'Return only JSON: {"text":"..."}.\n'
            f"Topic: {topic}\nTone: {tone}\nIssues: {', '.join(issues)}\nDraft: {text}"
        )
        try:
            response = client.chat.completions.create(
                model=self._queue_llm_model,
                messages=[
                    {"role": "system", "content": "You rewrite social copy and return JSON only."},
                    {"role": "user", "content": rewrite_prompt},
                ],
                max_tokens=150,
                temperature=0.5,
            )
            repaired = _extract_rewrite_text(response.choices[0].message.content)
            if repaired:
                candidate = sanitize_text(repaired, max_chars=500)
                if not quality_issues(candidate, min_chars=35, max_chars=500, forbid_url=True):
                    return candidate
        except Exception:
            logger.exception("Queue LLM quality rewrite failed")

        logger.warning("Queue LLM output failed quality guard; using fallback text.")
        return _fallback_queue_text(topic, tone)


def _extract_rewrite_text(raw: str | None) -> str | None:
    payload = (raw or "").strip()
    if not payload:
        return None
    try:
        import json

        parsed = json.loads(payload)
    except Exception:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            import json

            parsed = json.loads(payload[start : end + 1])
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    text = str(parsed.get("text", "")).strip()
    return text or None


def _fallback_queue_text(topic: str, tone: str) -> str:
    topic_clean = sanitize_text(topic, max_chars=80) or "this topic"
    tone_clean = sanitize_text(tone, max_chars=30) or "thoughtful"
    return f"{topic_clean} keeps getting more interesting. I am leaning {tone_clean} on this one, but what am I missing?"
