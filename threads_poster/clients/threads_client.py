from __future__ import annotations

import logging
import time
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

    def _require_access_token(self) -> None:
        if self._access_token:
            return
        raise ValueError(
            "THREADS_ACCESS_TOKEN is missing. Check .env encoding and token configuration."
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def create_media_container(
        self,
        user_id: str,
        text: str,
        media_type: str,
        media_url: str | None = None,
        **options: Any,
    ) -> str:
        self._require_access_token()
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
        self._log_error_response(response, "create_media_container")
        response.raise_for_status()
        data = response.json()
        creation_id = data.get("id") or data.get("creation_id")
        if not creation_id:
            raise ValueError("Threads API did not return a creation_id")
        return creation_id

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def publish_post(self, user_id: str, creation_id: str) -> tuple[str, str | None]:
        self._require_access_token()
        url = f"{self._base_url}/{user_id}/threads_publish"
        payload = {"creation_id": creation_id, "access_token": self._access_token}
        response = self._client.post(url, data=payload)
        self._log_error_response(response, "publish_post")
        response.raise_for_status()
        data = response.json()
        media_id = data.get("id")
        if not media_id:
            raise ValueError("Threads API did not return a post id")
        permalink = (
            data.get("permalink")
            or data.get("permalink_url")
            or data.get("url")
            or data.get("post_url")
        )
        return media_id, permalink

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_user_insights(
        self,
        user_id: str,
        metrics: list[str],
        since: int | None = None,
        breakdown: str | None = None,
    ) -> dict[str, Any]:
        self._require_access_token()
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
        self._log_error_response(response, "get_user_insights")
        response.raise_for_status()
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def get_post_insights(self, media_id: str, metrics: list[str]) -> dict[str, Any]:
        self._require_access_token()
        url = f"{self._base_url}/{media_id}/insights"
        params = {"metric": ",".join(metrics), "access_token": self._access_token}
        response = self._client.get(url, params=params)
        self._log_error_response(response, "get_post_insights")
        response.raise_for_status()
        return response.json()

    def get_trending_topics(
        self,
        limit: int = 5,
        locale: str = "en_US",
        country_code: str | None = None,
    ) -> list[str]:
        self._require_access_token()
        url = f"{self._base_url}/trending_topics"
        settings = get_settings()
        country_code = country_code or settings.threads_default_country_code
        params = {
            "limit": limit,
            "locale": locale,
            "country_code": country_code,
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "get_trending_topics")
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            error = payload["error"]
            msg = f"{error.get('message')} (code {error.get('code')})"
            raise ValueError(f"Trending topic call failed: {msg}")
        data = payload.get("data", [])
        topics: list[str] = []
        for entry in data:
            candidate = (
                entry.get("topic")
                or entry.get("name")
                or entry.get("keyword")
                or entry.get("label")
            )
            if candidate:
                topics.append(str(candidate))
        return topics[:limit]

    def keyword_search(self, query: str, search_type: str = "RECENT", limit: int = 10) -> list[dict[str, Any]]:
        self._require_access_token()
        url = f"{self._base_url}/keyword_search"
        params = {
            "q": query,
            "search_type": search_type,
            "limit": limit,
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "keyword_search")
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])

    def reply_to_post(self, post_id: str, text: str, **options: Any) -> str:
        self._require_access_token()
        user_id = str(options.pop("user_id", "me") or "me")
        prefer_container = bool(options.pop("prefer_container", True))
        allow_direct_fallback = bool(options.pop("allow_direct_fallback", True))
        media_type = str(options.pop("media_type", "TEXT") or "TEXT")
        reply_control = options.pop("reply_control", None)
        if prefer_container:
            try:
                return self._reply_via_container(
                    user_id=user_id,
                    parent_post_id=post_id,
                    text=text,
                    media_type=media_type,
                    reply_control=reply_control,
                    extra_options=options,
                )
            except Exception:
                if not allow_direct_fallback:
                    raise
                logger.warning(
                    "Container reply failed for post_id=%s; attempting direct /replies fallback.",
                    post_id,
                )
        return self._reply_via_direct_replies(post_id=post_id, text=text, extra_options=options)

    def get_me(self) -> dict[str, Any]:
        self._require_access_token()
        url = f"{self._base_url}/me"
        params = {"fields": "id,username", "access_token": self._access_token}
        response = self._client.get(url, params=params)
        self._log_error_response(response, "get_me")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def list_replies(self, post_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._require_access_token()
        url = f"{self._base_url}/{post_id}/replies"
        params = {
            "limit": max(1, min(limit, 100)),
            "fields": (
                "id,text,reply_text,username,timestamp,"
                "has_replies,root_post,replied_to,is_reply,is_reply_owned_by_me,hide_status"
            ),
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "list_replies")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [item for item in data if isinstance(item, dict)]

    def list_user_replies(self, user_id: str = "me", limit: int = 50) -> list[dict[str, Any]]:
        self._require_access_token()
        url = f"{self._base_url}/{user_id}/replies"
        params = {
            "limit": max(1, min(limit, 100)),
            "fields": (
                "id,text,reply_text,username,timestamp,permalink,shortcode,"
                "root_post,replied_to,is_reply,hide_status"
            ),
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "list_user_replies")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [item for item in data if isinstance(item, dict)]

    def list_user_threads(self, user_id: str = "me", limit: int = 25) -> list[dict[str, Any]]:
        self._require_access_token()
        url = f"{self._base_url}/{user_id}/threads"
        params = {
            "limit": max(1, min(limit, 100)),
            "fields": (
                "id,text,timestamp,permalink,media_type,has_replies,is_reply,"
                "reply_audience,username"
            ),
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "list_user_threads")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [item for item in data if isinstance(item, dict)]

    def list_conversation(self, post_id: str, limit: int = 100, reverse: bool = False) -> list[dict[str, Any]]:
        self._require_access_token()
        url = f"{self._base_url}/{post_id}/conversation"
        params = {
            "limit": max(1, min(limit, 100)),
            "reverse": str(bool(reverse)).lower(),
            "fields": (
                "id,text,reply_text,username,timestamp,"
                "has_replies,root_post,replied_to,is_reply,is_reply_owned_by_me,hide_status"
            ),
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "list_conversation")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        return [item for item in data if isinstance(item, dict)]

    def get_media_container_status(self, container_id: str) -> dict[str, Any]:
        self._require_access_token()
        url = f"{self._base_url}/{container_id}"
        params = {
            "fields": "id,status,error_message",
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "get_media_container_status")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_threads_publishing_limit(self, user_id: str = "me") -> dict[str, Any]:
        self._require_access_token()
        url = f"{self._base_url}/{user_id}/threads_publishing_limit"
        params = {
            "fields": "reply_quota_usage,reply_config",
            "access_token": self._access_token,
        }
        response = self._client.get(url, params=params)
        self._log_error_response(response, "get_threads_publishing_limit")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def manage_reply(self, reply_id: str, hide: bool = True) -> bool:
        self._require_access_token()
        url = f"{self._base_url}/{reply_id}/manage_reply"
        payload = {
            "hide": "true" if hide else "false",
            "access_token": self._access_token,
        }
        response = self._client.post(url, data=payload)
        self._log_error_response(response, "manage_reply")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "success" in data:
            return bool(data["success"])
        return True

    def _reply_via_container(
        self,
        *,
        user_id: str,
        parent_post_id: str,
        text: str,
        media_type: str,
        reply_control: str | None,
        extra_options: dict[str, Any],
    ) -> str:
        create_options: dict[str, Any] = dict(extra_options)
        create_options["reply_to_id"] = parent_post_id
        if reply_control:
            create_options["reply_control"] = reply_control
        creation_id = self.create_media_container(
            user_id=user_id,
            text=text,
            media_type=media_type,
            **create_options,
        )
        media_id, _ = self._publish_with_container_wait(
            user_id=user_id,
            creation_id=creation_id,
        )
        return media_id

    def _reply_via_direct_replies(
        self,
        *,
        post_id: str,
        text: str,
        extra_options: dict[str, Any],
    ) -> str:
        url = f"{self._base_url}/{post_id}/replies"
        payload: dict[str, Any] = {"reply_text": text, "access_token": self._access_token}
        payload.update(extra_options)
        response = self._client.post(url, data=payload)
        self._log_error_response(response, "reply_to_post_direct")
        response.raise_for_status()
        data = response.json()
        reply_id = data.get("id") or data.get("reply_id")
        if not reply_id:
            raise ValueError("Threads API did not return a reply id")
        return str(reply_id)

    def _publish_with_container_wait(
        self,
        *,
        user_id: str,
        creation_id: str,
    ) -> tuple[str, str | None]:
        wait_seconds = [0, 2, 5, 10, 15]
        last_exception: Exception | None = None
        for wait_for in wait_seconds:
            if wait_for:
                time.sleep(wait_for)
            try:
                return self.publish_post(user_id=user_id, creation_id=creation_id)
            except Exception as exc:
                last_exception = exc
                if not self._is_container_pending_error(exc):
                    raise
                try:
                    status_payload = self.get_media_container_status(creation_id)
                    status_value = str(status_payload.get("status") or "").upper()
                    if status_value in {"ERROR", "EXPIRED"}:
                        raise
                except Exception:
                    continue
        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to publish reply container for an unknown reason.")

    def _is_container_pending_error(self, exc: Exception) -> bool:
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        message = str(exc).lower()
        if "not ready" in message or "in progress" in message:
            return True
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                err_message = str(error.get("message") or "").lower()
                if "not ready" in err_message or "in progress" in err_message:
                    return True
        return False

    def _log_error_response(self, response: httpx.Response, context: str) -> None:
        if response.status_code < 400:
            return
        details: list[str] = [f"status={response.status_code}"]
        auth_header = response.headers.get("www-authenticate")
        if auth_header:
            details.append(f"www_authenticate={auth_header}")
        payload: dict[str, Any] | None = None
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
            error = payload.get("error") or payload.get("errors")
            if isinstance(error, dict):
                message = error.get("message")
                if message:
                    details.append(f"message={message}")
                if code := error.get("code"):
                    details.append(f"code={code}")
                if err_type := error.get("type"):
                    details.append(f"type={err_type}")
                if fbtrace := error.get("fbtrace_id"):
                    details.append(f"fbtrace={fbtrace}")
            elif error:
                details.append(f"error={error}")
            elif payload:
                details.append(f"payload={payload}")
        if not details:
            text = response.text.strip()
            if text:
                details.append(f"text={text}")
        if not details:
            details.append(f"status={response.status_code}")
        logger.warning("Threads API %s failed: %s", context, "; ".join(details))
