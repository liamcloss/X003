from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()


def _normalize_bom_env_keys() -> None:
    """Mirror BOM-prefixed env vars to their plain key names.

    Some editors save `.env` with UTF-8 BOM, which can turn the first key into
    `\\ufeffKEY`. That silently breaks lookups like `os.getenv("KEY")`.
    """
    for key in list(os.environ.keys()):
        if not key.startswith("\ufeff"):
            continue
        normalized = key.lstrip("\ufeff")
        if not normalized:
            continue
        value = os.environ.get(key, "")
        current = os.environ.get(normalized)
        if current is None or current == "":
            os.environ[normalized] = value


_normalize_bom_env_keys()


DEFAULT_TRENDING_REPLY_KEYWORDS = [
    "current affairs",
    "culture",
    "motivation",
    "inspiration",
    "playful insights",
]


@dataclass(frozen=True)
class Settings:
    threads_access_token: str
    threads_app_id: str
    threads_app_secret: str
    openai_api_key: str
    default_timezone: str
    database_url: str
    scheduler_timezone: str
    openai_token_cost_per_1k: float
    allow_llm_for_queue: bool
    predefined_posts_csv: str
    predefined_posts_replenish_enabled: bool
    predefined_posts_replenish_days: int
    predefined_posts_replenish_per_day: int
    predefined_posts_replenish_interval_hours: int
    threads_default_user_id: str
    threads_default_country_code: str
    threads_cap_reply_to_reply_ids: bool
    threads_cap_manage_reply: bool
    threads_cap_keyword_search: bool
    threads_cap_trending_topics: bool
    threads_cap_follower_demographics: bool
    ops_capability_probe_enabled: bool
    ops_capability_probe_interval_hours: int
    ops_capability_probe_timeout_seconds: int
    ops_capability_probe_output_dir: str
    ops_capability_probe_read_only: bool
    ops_capability_probe_auto_sync: bool
    ops_capability_promote_passes: int
    ops_capability_demote_fails: int
    ops_kpi_snapshot_enabled: bool
    ops_kpi_snapshot_interval_hours: int
    ops_kpi_snapshot_lookback_days: int
    ops_kpi_snapshot_refresh_recent: bool
    ops_kpi_snapshot_refresh_limit: int
    ops_kpi_snapshot_out: str
    ops_auto_log_path: str
    trending_reply_enabled: bool
    trending_reply_interval_hours: int
    trending_reply_per_hour: int
    trending_reply_batch_size: int
    trending_reply_topics_limit: int
    trending_reply_search_limit: int
    trending_reply_search_type: str
    trending_reply_keywords: list[str]
    trending_reply_keyword_search_enabled: bool
    auto_engage_reply_enabled: bool
    auto_engage_reply_interval_minutes: int
    auto_engage_reply_per_hour: int
    auto_engage_reply_batch_size: int
    auto_engage_reply_lookback_days: int
    auto_engage_reply_posts_limit: int
    auto_engage_reply_scan_replies_limit: int
    auto_engage_reply_scan_conversation_enabled: bool
    auto_engage_reply_max_comment_age_hours: int
    auto_engage_reply_allow_direct_fallback: bool
    auto_engage_reply_reply_control: str
    auto_engage_reply_fallback_to_post: bool
    auto_engage_reply_fallback_posts_per_run: int


def get_settings() -> Settings:
    return Settings(
        threads_access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
        threads_app_id=os.getenv("THREADS_APP_ID", ""),
        threads_app_secret=os.getenv("THREADS_APP_SECRET", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "UTC"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./threads.db"),
        scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "UTC"),
        openai_token_cost_per_1k=_parse_float(
            os.getenv("OPENAI_TOKEN_COST_PER_1K", "0")
        ),
        allow_llm_for_queue=os.getenv("ALLOW_LLM_FOR_QUEUE", "false").lower() in {
            "1",
            "true",
            "yes",
        },
        predefined_posts_csv=os.getenv(
            "PREDEFINED_POSTS_CSV",
            str(
                Path(__file__).resolve().parents[2] / "examples" / "predefined_posts.csv"
            ),
        ),
        predefined_posts_replenish_enabled=(
            os.getenv("PREDEFINED_POSTS_REPLENISH_ENABLED", "true").lower() in {"1", "true", "yes"}
        ),
        predefined_posts_replenish_days=_parse_int(
            os.getenv("PREDEFINED_POSTS_REPLENISH_DAYS"), 3
        ),
        predefined_posts_replenish_per_day=_parse_int(
            os.getenv("PREDEFINED_POSTS_REPLENISH_PER_DAY"), 4
        ),
        predefined_posts_replenish_interval_hours=_parse_int(
            os.getenv("PREDEFINED_POSTS_REPLENISH_INTERVAL_HOURS"), 12
        ),
        threads_default_user_id=os.getenv("THREADS_DEFAULT_USER_ID", "me"),
        threads_default_country_code=os.getenv("THREADS_DEFAULT_COUNTRY_CODE", "US"),
        threads_cap_reply_to_reply_ids=(
            os.getenv("THREADS_CAP_REPLY_TO_REPLY_IDS", "false").lower()
            in {"1", "true", "yes"}
        ),
        threads_cap_manage_reply=(
            os.getenv("THREADS_CAP_MANAGE_REPLY", "false").lower()
            in {"1", "true", "yes"}
        ),
        threads_cap_keyword_search=(
            os.getenv("THREADS_CAP_KEYWORD_SEARCH", "false").lower()
            in {"1", "true", "yes"}
        ),
        threads_cap_trending_topics=(
            os.getenv("THREADS_CAP_TRENDING_TOPICS", "false").lower()
            in {"1", "true", "yes"}
        ),
        threads_cap_follower_demographics=(
            os.getenv("THREADS_CAP_FOLLOWER_DEMOGRAPHICS", "false").lower()
            in {"1", "true", "yes"}
        ),
        ops_capability_probe_enabled=(
            os.getenv("OPS_CAPABILITY_PROBE_ENABLED", "true").lower()
            in {"1", "true", "yes"}
        ),
        ops_capability_probe_interval_hours=_parse_int(
            os.getenv("OPS_CAPABILITY_PROBE_INTERVAL_HOURS"), 24
        ),
        ops_capability_probe_timeout_seconds=_parse_int(
            os.getenv("OPS_CAPABILITY_PROBE_TIMEOUT_SECONDS"), 420
        ),
        ops_capability_probe_output_dir=os.getenv(
            "OPS_CAPABILITY_PROBE_OUTPUT_DIR",
            "notes/strategy",
        ),
        ops_capability_probe_read_only=(
            os.getenv("OPS_CAPABILITY_PROBE_READ_ONLY", "true").lower()
            in {"1", "true", "yes"}
        ),
        ops_capability_probe_auto_sync=(
            os.getenv("OPS_CAPABILITY_PROBE_AUTO_SYNC", "true").lower()
            in {"1", "true", "yes"}
        ),
        ops_capability_promote_passes=_parse_int(
            os.getenv("OPS_CAPABILITY_PROMOTE_PASSES"), 2
        ),
        ops_capability_demote_fails=_parse_int(
            os.getenv("OPS_CAPABILITY_DEMOTE_FAILS"), 1
        ),
        ops_kpi_snapshot_enabled=(
            os.getenv("OPS_KPI_SNAPSHOT_ENABLED", "true").lower()
            in {"1", "true", "yes"}
        ),
        ops_kpi_snapshot_interval_hours=_parse_int(
            os.getenv("OPS_KPI_SNAPSHOT_INTERVAL_HOURS"), 24
        ),
        ops_kpi_snapshot_lookback_days=_parse_int(
            os.getenv("OPS_KPI_SNAPSHOT_LOOKBACK_DAYS"), 14
        ),
        ops_kpi_snapshot_refresh_recent=(
            os.getenv("OPS_KPI_SNAPSHOT_REFRESH_RECENT", "true").lower()
            in {"1", "true", "yes"}
        ),
        ops_kpi_snapshot_refresh_limit=_parse_int(
            os.getenv("OPS_KPI_SNAPSHOT_REFRESH_LIMIT"), 20
        ),
        ops_kpi_snapshot_out=os.getenv(
            "OPS_KPI_SNAPSHOT_OUT",
            "notes/strategy/learning_profile_snapshots.jsonl",
        ),
        ops_auto_log_path=os.getenv(
            "OPS_AUTO_LOG_PATH",
            "notes/strategy/auto_ops_log.jsonl",
        ),
        trending_reply_enabled=os.getenv("TRENDING_REPLY_ENABLED", "false").lower() in {
            "1",
            "true",
            "yes",
        },
        trending_reply_interval_hours=_parse_int(
            os.getenv("TRENDING_REPLY_INTERVAL_HOURS"), 1
        ),
        trending_reply_per_hour=_parse_int(
            os.getenv("TRENDING_REPLY_PER_HOUR"), 4
        ),
        trending_reply_batch_size=_parse_int(
            os.getenv("TRENDING_REPLY_BATCH_SIZE"), 1
        ),
        trending_reply_topics_limit=_parse_int(
            os.getenv("TRENDING_REPLY_TOPICS_LIMIT"), 5
        ),
        trending_reply_search_limit=_parse_int(
            os.getenv("TRENDING_REPLY_SEARCH_LIMIT"), 10
        ),
        trending_reply_search_type=os.getenv("TRENDING_REPLY_SEARCH_TYPE", "RECENT"),
        trending_reply_keywords=_parse_keywords(
            os.getenv("TRENDING_REPLY_KEYWORDS"), DEFAULT_TRENDING_REPLY_KEYWORDS
        ),
        trending_reply_keyword_search_enabled=(
            os.getenv("TRENDING_REPLY_KEYWORD_SEARCH_ENABLED", "false").lower()
            in {"1", "true", "yes"}
        ),
        auto_engage_reply_enabled=(
            os.getenv("AUTO_ENGAGE_REPLY_ENABLED", "true").lower()
            in {"1", "true", "yes"}
        ),
        auto_engage_reply_interval_minutes=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_INTERVAL_MINUTES"), 15
        ),
        auto_engage_reply_per_hour=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_PER_HOUR"), 8
        ),
        auto_engage_reply_batch_size=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_BATCH_SIZE"), 3
        ),
        auto_engage_reply_lookback_days=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_LOOKBACK_DAYS"), 7
        ),
        auto_engage_reply_posts_limit=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_POSTS_LIMIT"), 25
        ),
        auto_engage_reply_scan_replies_limit=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_SCAN_REPLIES_LIMIT"), 50
        ),
        auto_engage_reply_scan_conversation_enabled=(
            os.getenv("AUTO_ENGAGE_REPLY_SCAN_CONVERSATION_ENABLED", "true").lower()
            in {"1", "true", "yes"}
        ),
        auto_engage_reply_max_comment_age_hours=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_MAX_COMMENT_AGE_HOURS"), 72
        ),
        auto_engage_reply_allow_direct_fallback=(
            os.getenv("AUTO_ENGAGE_REPLY_ALLOW_DIRECT_FALLBACK", "true").lower()
            in {"1", "true", "yes"}
        ),
        auto_engage_reply_reply_control=os.getenv("AUTO_ENGAGE_REPLY_REPLY_CONTROL", "EVERYONE"),
        auto_engage_reply_fallback_to_post=(
            os.getenv("AUTO_ENGAGE_REPLY_FALLBACK_TO_POST", "true").lower()
            in {"1", "true", "yes"}
        ),
        auto_engage_reply_fallback_posts_per_run=_parse_int(
            os.getenv("AUTO_ENGAGE_REPLY_FALLBACK_POSTS_PER_RUN"), 1
        ),
    )


def _parse_float(value: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_keywords(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    parts = [segment.strip() for segment in value.split(",")]
    keywords = [segment for segment in parts if segment]
    return keywords if keywords else default
