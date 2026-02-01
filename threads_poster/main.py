from __future__ import annotations

from fastapi import FastAPI

from threads_poster.api.routes import router
from threads_poster.core.logging import configure_logging
from threads_poster.core.scheduler import get_scheduler
from threads_poster.db.init_db import init_db


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

    @app.on_event("shutdown")
    async def shutdown_scheduler() -> None:
        if scheduler.running:
            scheduler.shutdown(wait=False)

    return app


app = create_app()
