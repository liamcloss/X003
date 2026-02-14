from __future__ import annotations

from fastapi import FastAPI

from threads_poster.api.routes import router
from threads_poster.core.logging import configure_logging
from threads_poster.core.scheduler import get_scheduler
from threads_poster.db.init_db import init_db
from threads_poster.services.auto_engagement_replier import register_auto_engagement_reply_job
from threads_poster.services.ops_automation import register_ops_automation_jobs
from threads_poster.services.predefined_posts_replenishment import register_predefined_posts_replenishment_job
from threads_poster.services.predefined_posts_scheduler import register_predefined_posts_scheduler_job
from threads_poster.services.scheduling import register_catch_up_job
from threads_poster.services.trending_replier import register_trending_reply_job
from threads_poster.services.token_maintenance import register_token_refresh_job


def create_app() -> FastAPI:
    configure_logging()
    init_db()
    app = FastAPI(title="Threads Posting System")
    app.include_router(router)

    scheduler = get_scheduler()

    @app.on_event("startup")
    async def start_scheduler() -> None:
        if not scheduler.running:
            scheduler.start()
        register_token_refresh_job()
        register_predefined_posts_replenishment_job()
        register_predefined_posts_scheduler_job()
        register_trending_reply_job()
        register_auto_engagement_reply_job()
        register_ops_automation_jobs()
        register_catch_up_job()

    @app.on_event("shutdown")
    async def shutdown_scheduler() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    return app


app = create_app()
