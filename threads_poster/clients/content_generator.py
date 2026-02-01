from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI

from threads_poster.core.config import get_settings

logger = logging.getLogger(__name__)


class ContentGenerator:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for content generation")
        self._client = OpenAI(api_key=settings.openai_api_key)

    def generate_post(self, topic: str, tone: str) -> dict[str, Any]:
        prompt = (
            "Create a concise Threads post (max 500 characters) about the topic provided. "
            "Provide a short caption and describe an image concept suitable for the post."
        )
        chat_response = self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a social media copywriter."},
                {"role": "user", "content": f"Topic: {topic}\nTone: {tone}\n{prompt}"},
            ],
            max_tokens=220,
        )
        text = chat_response.choices[0].message.content.strip()

        image_prompt = (
            "Create a vibrant, engaging photo-style image that matches this Threads post: "
            f"{text}"
        )
        media_url = None
        media_type = "IMAGE"
        try:
            image_response = self._client.images.generate(
                model="gpt-image-1",
                prompt=image_prompt,
                size="1024x1024",
            )
            media_url = image_response.data[0].url
        except Exception:
            logger.exception("Failed to generate image, falling back to text-only post")
            media_type = "TEXT"

        return {
            "text": text,
            "media_type": media_type,
            "media_url": media_url,
        }
