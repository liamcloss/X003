from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from threads_poster.core.config import get_settings

logger = logging.getLogger(__name__)


class ThreadsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._access_token = settings.threads_access_token
        self._base_url = "https://graph.threads.net"
        self._client = httpx.Client(timeout=30)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def create_media_container(
        self,
        user_id: str,
        text: str,
        media_type: str,
        media_url: str | None = None,
        **options: Any,
    ) -> str:
        payload: dict[str, Any] = {
            "text": text,
            "media_type": media_type,
        }
        if media_url:
            if media_type == "IMAGE":
                payload["image_url"] = media_url
            elif media_type == "VIDEO":
                payload["video_url"] = media_url
            else:
                payload["media_url"] = media_url
        payload.update(options)
        payload["access_token"] = self._access_token

        url = f"{self._base_url}/{user_id}/threads"
        response = self._client.post(url, data=payload)
        response.raise_for_status()
        data = response.json()
        creation_id = data.get("id") or data.get("creation_id")
        if not creation_id:
            raise ValueError("Threads API did not return a creation_id")
        return creation_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def publish_post(self, user_id: str, creation_id: str) -> str:
        url = f"{self._base_url}/{user_id}/threads_publish"
        payload = {"creation_id": creation_id, "access_token": self._access_token}
        response = self._client.post(url, data=payload)
        response.raise_for_status()
        data = response.json()
        media_id = data.get("id")
        if not media_id:
            raise ValueError("Threads API did not return a post id")
        return media_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_user_insights(
        self,
        user_id: str,
        metrics: list[str],
        since: int | None = None,
        breakdown: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{user_id}/threads_insights"
        params: dict[str, Any] = {
            "metric": ",".join(metrics),
            "access_token": self._access_token,
        }
        if since is not None:
            params["since"] = since
        if breakdown is not None:
            params["breakdown"] = breakdown
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_post_insights(self, media_id: str, metrics: list[str]) -> dict[str, Any]:
        url = f"{self._base_url}/{media_id}/insights"
        params = {"metric": ",".join(metrics), "access_token": self._access_token}
        response = self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()
