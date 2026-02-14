from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy import select
from tenacity import RetryError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from threads_poster.clients.threads_client import ThreadsClient
from threads_poster.core.config import get_settings
from threads_poster.db.models import Post, PostStatus
from threads_poster.db.session import SessionLocal


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now_utc().strftime("%Y%m%dT%H%M%SZ")


def _safe_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RetryError):
        last = exc.last_attempt.exception()
        if isinstance(last, Exception):
            return _safe_error(last)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = None
        message = str(exc)
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                code = err.get("code")
                if err.get("message"):
                    message = str(err["message"])
        return {"type": type(exc).__name__, "status": status, "code": code, "message": message}
    return {"type": type(exc).__name__, "message": str(exc)}


def _preview_payload(value: Any) -> Any:
    if isinstance(value, list):
        sample = value[:2]
        return {"count": len(value), "sample": sample}
    if isinstance(value, dict):
        if "data" in value and isinstance(value["data"], list):
            sample = value["data"][:2]
            return {"keys": sorted(value.keys()), "data_count": len(value["data"]), "data_sample": sample}
        keys = sorted(value.keys())
        subset: dict[str, Any] = {}
        for key in (
            "id",
            "username",
            "permalink",
            "status",
            "error_message",
            "reply_quota_usage",
            "reply_config",
        ):
            if key in value:
                subset[key] = value[key]
        if subset:
            subset["keys"] = keys
            return subset
        return {"keys": keys}
    return value


def _latest_published_media_id() -> str | None:
    with SessionLocal() as session:
        statement = (
            select(Post.media_id)
            .where(
                Post.status == PostStatus.published,
                Post.media_id.is_not(None),
            )
            .order_by(Post.published_time.desc())
            .limit(1)
        )
        value = session.scalar(statement)
    if not value:
        return None
    return str(value)

def _run_step(
    name: str,
    required: bool,
    fn: Callable[[], Any],
    results: list[dict[str, Any]],
) -> tuple[bool, Any]:
    try:
        payload = fn()
        results.append(
            {
                "name": name,
                "required": required,
                "ok": True,
                "error": None,
                "preview": _preview_payload(payload),
            }
        )
        return True, payload
    except Exception as exc:  # pragma: no cover - runtime integration path
        results.append(
            {
                "name": name,
                "required": required,
                "ok": False,
                "error": _safe_error(exc),
                "preview": None,
            }
        )
        return False, None


def _run_skip(
    name: str,
    required: bool,
    reason: str,
    results: list[dict[str, Any]],
) -> None:
    results.append(
        {
            "name": name,
            "required": required,
            "ok": not required,
            "error": {"type": "Skipped", "message": reason},
            "preview": None,
        }
    )


def _print_summary(results: list[dict[str, Any]], report_path: Path) -> None:
    print("")
    print("Threads API Verification Summary")
    print("--------------------------------")
    for row in results:
        marker = "PASS" if row["ok"] else "FAIL"
        req = "required" if row["required"] else "optional"
        print(f"{marker:4}  {req:8}  {row['name']}")
        if row["error"]:
            message = row["error"].get("message") or row["error"]
            code = row["error"].get("code")
            status = row["error"].get("status")
            if status is not None or code is not None:
                print(f"      status={status} code={code} message={message}")
            else:
                print(f"      message={message}")
    failures = [row for row in results if row["required"] and not row["ok"]]
    print("")
    print(f"Required failures: {len(failures)}")
    print(f"Report: {report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Threads API verification calls for app testing/review and write "
            "a structured JSON report."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="notes/strategy",
        help="Directory to write the verification JSON report.",
    )
    parser.add_argument(
        "--require-keyword-search",
        action="store_true",
        help="Treat keyword_search call as required.",
    )
    parser.add_argument(
        "--require-trending-topics",
        action="store_true",
        help="Treat get_trending_topics call as required.",
    )
    parser.add_argument(
        "--skip-manage-reply",
        action="store_true",
        help="Skip hide/unhide manage_reply calls.",
    )
    parser.add_argument(
        "--require-manage-reply",
        action="store_true",
        help="Treat manage_reply hide/unhide calls as required regardless of capability flags.",
    )
    parser.add_argument(
        "--require-reply-to-reply",
        action="store_true",
        help="Treat nested reply (replying to a reply id) as required regardless of capability flags.",
    )
    parser.add_argument(
        "--skip-reply-to-reply",
        action="store_true",
        help="Skip nested reply capability probe.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Run non-mutating checks only (no publish/reply/manage-reply calls).",
    )
    args = parser.parse_args()

    settings = get_settings()
    client = ThreadsClient()
    token = _stamp()
    results: list[dict[str, Any]] = []
    context: dict[str, Any] = {"token": token}

    require_manage_reply = bool(settings.threads_cap_manage_reply or args.require_manage_reply)
    require_reply_to_reply = bool(settings.threads_cap_reply_to_reply_ids or args.require_reply_to_reply)
    require_keyword_search = bool(settings.threads_cap_keyword_search or args.require_keyword_search)
    require_trending_topics = bool(settings.threads_cap_trending_topics or args.require_trending_topics)
    require_follower_demographics = bool(settings.threads_cap_follower_demographics)
    probe_mode = "read_only" if args.read_only else "active"
    if args.read_only:
        require_manage_reply = False
        require_reply_to_reply = False

    ok, me_payload = _run_step(
        "threads_basic:get_me",
        True,
        lambda: client.get_me(),
        results,
    )
    if ok and isinstance(me_payload, dict):
        context["user_id"] = str(me_payload.get("id") or "").strip() or settings.threads_default_user_id or "me"
    else:
        context["user_id"] = settings.threads_default_user_id or "me"
    user_id = str(context["user_id"])

    _run_step(
        "threads_basic:list_user_threads",
        True,
        lambda: client.list_user_threads(user_id=user_id, limit=5),
        results,
    )

    _run_step(
        "threads_manage_replies:get_threads_publishing_limit",
        True,
        lambda: client.get_threads_publishing_limit(user_id=user_id),
        results,
    )

    target_media_id = ""
    if args.read_only:
        _run_skip(
            "threads_content_publish:create_media_container",
            False,
            "Skipped by --read-only.",
            results,
        )
        _run_skip(
            "threads_content_publish:get_media_container_status",
            False,
            "Skipped by --read-only.",
            results,
        )
        _run_skip(
            "threads_content_publish:publish_post",
            False,
            "Skipped by --read-only.",
            results,
        )
        fallback = _latest_published_media_id()
        if fallback:
            target_media_id = fallback
            context["published_media_id"] = fallback
    else:
        post_text = "What tiny workflow tweak saved you the most time this week?"
        ok, creation_id = _run_step(
            "threads_content_publish:create_media_container",
            True,
            lambda: client.create_media_container(
                user_id=user_id,
                text=post_text,
                media_type="TEXT",
                reply_control="EVERYONE",
            ),
            results,
        )
        if ok:
            context["creation_id"] = creation_id

        if context.get("creation_id"):
            _run_step(
                "threads_content_publish:get_media_container_status",
                False,
                lambda: client.get_media_container_status(str(context["creation_id"])),
                results,
            )
            ok, publish_payload = _run_step(
                "threads_content_publish:publish_post",
                True,
                lambda: client.publish_post(user_id=user_id, creation_id=str(context["creation_id"])),
                results,
            )
            if ok and isinstance(publish_payload, tuple):
                media_id, permalink = publish_payload
                context["published_media_id"] = str(media_id)
                context["published_permalink"] = permalink
        else:
            _run_skip(
                "threads_content_publish:publish_post",
                True,
                "Skipped because media container creation failed.",
                results,
            )

        target_media_id = str(context.get("published_media_id") or "")
        if not target_media_id:
            fallback = _latest_published_media_id()
            if fallback:
                target_media_id = fallback
                context["published_media_id"] = fallback

    post_insights_required = not args.read_only
    if target_media_id:
        ok, _ = _run_step(
            "threads_manage_insights:get_post_insights",
            post_insights_required,
            lambda: client.get_post_insights(
                target_media_id,
                ["views", "likes", "replies", "reposts", "quotes"],
            ),
            results,
        )
        if not ok:
            time.sleep(3)
            _run_step(
                "threads_manage_insights:get_post_insights_retry",
                post_insights_required,
                lambda: client.get_post_insights(
                    target_media_id,
                    ["views", "likes", "replies", "reposts", "quotes"],
                ),
                results,
            )
    else:
        _run_skip(
            "threads_manage_insights:get_post_insights",
            post_insights_required,
            "Skipped because no published media_id is available.",
            results,
        )

    _run_step(
        "threads_manage_insights:get_user_insights_views",
        True,
        lambda: client.get_user_insights(user_id, ["views"]),
        results,
    )
    _run_step(
        "threads_manage_insights:get_user_insights_followers_count",
        False,
        lambda: client.get_user_insights(user_id, ["followers_count"]),
        results,
    )
    _run_step(
        "threads_manage_insights:get_user_insights_follower_demographics",
        require_follower_demographics,
        lambda: client.get_user_insights(user_id, ["follower_demographics"], breakdown="country"),
        results,
    )

    reply_reads_required = not args.read_only
    if target_media_id:
        _run_step(
            "threads_read_replies:list_replies",
            reply_reads_required,
            lambda: client.list_replies(target_media_id, limit=20),
            results,
        )
        _run_step(
            "threads_read_replies:list_conversation",
            reply_reads_required,
            lambda: client.list_conversation(target_media_id, limit=20, reverse=False),
            results,
        )
    else:
        _run_skip(
            "threads_read_replies:list_replies",
            reply_reads_required,
            "Skipped because no published media_id is available.",
            results,
        )
        _run_skip(
            "threads_read_replies:list_conversation",
            reply_reads_required,
            "Skipped because no published media_id is available.",
            results,
        )

    _run_step(
        "threads_read_replies:list_user_replies",
        True,
        lambda: client.list_user_replies(user_id=user_id, limit=20),
        results,
    )

    reply_id_for_manage = ""
    if args.read_only:
        _run_skip(
            "threads_manage_replies:reply_to_post",
            False,
            "Skipped by --read-only.",
            results,
        )
    elif target_media_id:
        ok, reply_media_id = _run_step(
            "threads_manage_replies:reply_to_post",
            True,
            lambda: client.reply_to_post(
                target_media_id,
                "Interesting point. What part has been hardest to implement for you?",
                user_id=user_id,
                prefer_container=True,
                allow_direct_fallback=True,
                reply_control="EVERYONE",
            ),
            results,
        )
        if ok and reply_media_id:
            reply_id_for_manage = str(reply_media_id)
            context["reply_media_id"] = reply_id_for_manage
    else:
        _run_skip(
            "threads_manage_replies:reply_to_post",
            True,
            "Skipped because no published media_id is available.",
            results,
        )

    if args.read_only:
        _run_skip(
            "threads_read_replies:list_replies_after_reply",
            False,
            "Skipped by --read-only.",
            results,
        )
        _run_skip(
            "threads_read_replies:list_user_replies_after_reply",
            False,
            "Skipped by --read-only.",
            results,
        )
    else:
        if target_media_id:
            _run_step(
                "threads_read_replies:list_replies_after_reply",
                False,
                lambda: client.list_replies(target_media_id, limit=30),
                results,
            )
        else:
            _run_skip(
                "threads_read_replies:list_replies_after_reply",
                False,
                "Skipped because no published media_id is available.",
                results,
            )
        _run_step(
            "threads_read_replies:list_user_replies_after_reply",
            False,
            lambda: client.list_user_replies(user_id=user_id, limit=30),
            results,
        )

    if args.read_only:
        _run_skip(
            "threads_manage_replies:reply_to_reply",
            False,
            "Skipped by --read-only.",
            results,
        )
    elif args.skip_reply_to_reply:
        _run_skip(
            "threads_manage_replies:reply_to_reply",
            False,
            "Skipped by --skip-reply-to-reply.",
            results,
        )
    elif reply_id_for_manage:
        _run_step(
            "threads_manage_replies:reply_to_reply",
            require_reply_to_reply,
            lambda: client.reply_to_post(
                reply_id_for_manage,
                "Good follow-up. Which part should we break down first?",
                user_id=user_id,
                prefer_container=True,
                allow_direct_fallback=True,
                reply_control="EVERYONE",
            ),
            results,
        )
    else:
        _run_skip(
            "threads_manage_replies:reply_to_reply",
            require_reply_to_reply,
            "Could not locate reply id for nested reply probe.",
            results,
        )

    if args.read_only:
        _run_skip(
            "threads_manage_replies:manage_reply_hide",
            False,
            "Skipped by --read-only.",
            results,
        )
        _run_skip(
            "threads_manage_replies:manage_reply_unhide",
            False,
            "Skipped by --read-only.",
            results,
        )
    elif args.skip_manage_reply:
        _run_skip(
            "threads_manage_replies:manage_reply_hide",
            False,
            "Skipped by --skip-manage-reply.",
            results,
        )
        _run_skip(
            "threads_manage_replies:manage_reply_unhide",
            False,
            "Skipped by --skip-manage-reply.",
            results,
        )
    elif reply_id_for_manage:
        _run_step(
            "threads_manage_replies:manage_reply_hide",
            require_manage_reply,
            lambda: client.manage_reply(reply_id_for_manage, hide=True),
            results,
        )
        _run_step(
            "threads_manage_replies:manage_reply_unhide",
            require_manage_reply,
            lambda: client.manage_reply(reply_id_for_manage, hide=False),
            results,
        )
    else:
        _run_skip(
            "threads_manage_replies:manage_reply_hide",
            require_manage_reply,
            "Could not locate reply id for manage_reply from fresh reply reads.",
            results,
        )
        _run_skip(
            "threads_manage_replies:manage_reply_unhide",
            require_manage_reply,
            "Could not locate reply id for manage_reply from fresh reply reads.",
            results,
        )

    _run_step(
        "threads_keyword_search:keyword_search",
        require_keyword_search,
        lambda: client.keyword_search(token, search_type="RECENT", limit=10),
        results,
    )

    _run_step(
        "threads_discovery:get_trending_topics",
        require_trending_topics,
        lambda: client.get_trending_topics(
            limit=5,
            locale="en_US",
            country_code=settings.threads_default_country_code,
        ),
        results,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"threads_api_verification_{token}.json"
    required_failures = [row for row in results if row["required"] and not row["ok"]]
    report = {
        "timestamp_utc": _now_utc().isoformat(),
        "user_id": user_id,
        "token_marker": token,
        "probe_mode": probe_mode,
        "overall_pass": len(required_failures) == 0,
        "required_failures": required_failures,
        "results": results,
        "context": {
            "published_media_id": context.get("published_media_id"),
            "published_permalink": context.get("published_permalink"),
            "reply_media_id": context.get("reply_media_id"),
        },
        "capability_flags": {
            "threads_cap_reply_to_reply_ids": settings.threads_cap_reply_to_reply_ids,
            "threads_cap_manage_reply": settings.threads_cap_manage_reply,
            "threads_cap_keyword_search": settings.threads_cap_keyword_search,
            "threads_cap_trending_topics": settings.threads_cap_trending_topics,
            "threads_cap_follower_demographics": settings.threads_cap_follower_demographics,
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _print_summary(results, report_path)
    return 0 if len(required_failures) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
