import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
INSIGHTS_LOG = LOGS_DIR / "insights.log"
SCHEDULED_POSTS_LOG = LOGS_DIR / "scheduled_posts.log"
REPLIES_LOG = LOGS_DIR / "replies.log"
OPENAI_LOG = LOGS_DIR / "openai.log"


def _configure_rotating_logger(name: str, path: Path, fmt: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(fmt))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def configure_logging() -> None:
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    _configure_rotating_logger("threads_poster.insights", INSIGHTS_LOG, fmt)
    _configure_rotating_logger("threads_poster.scheduled_posts", SCHEDULED_POSTS_LOG, fmt)
    _configure_rotating_logger("threads_poster.replies", REPLIES_LOG, fmt)
    _configure_rotating_logger("threads_poster.openai", OPENAI_LOG, fmt)
