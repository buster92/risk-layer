"""
app/api/main.py

FastAPI application entry point.
Mounts all routers and starts APScheduler for daily jobs.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.routes import health, stocks, admin, alerts
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_all_tables

configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def _start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from app.jobs.daily_ingest import run_daily_ingest
    from app.jobs.daily_predict import run_daily_predict
    from app.jobs.daily_digest import run_daily_digest
    from app.jobs.weekly_eval import run_weekly_eval

    tz = settings.jobs_timezone
    scheduler = BackgroundScheduler(timezone=tz)

    scheduler.add_job(
        run_daily_ingest,
        CronTrigger(
            hour=settings.daily_ingest_hour,
            minute=settings.daily_ingest_minute,
            timezone=tz,
        ),
        id="daily_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_predict,
        CronTrigger(
            hour=settings.daily_predict_hour,
            minute=settings.daily_predict_minute,
            timezone=tz,
        ),
        id="daily_predict",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_digest,
        CronTrigger(
            hour=settings.daily_digest_hour,
            minute=settings.daily_digest_minute,
            timezone=tz,
        ),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekly_eval,
        CronTrigger(day_of_week="sun", hour=8, timezone=tz),
        id="weekly_eval",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started with 4 jobs")
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MoveCred API starting", version=settings.app_version)
    create_all_tables()
    scheduler = _start_scheduler()
    yield
    scheduler.shutdown(wait=False)
    logger.info("MoveCred API shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Active Stock Move Credibility Engine",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def root():
    """Redirect browsers to the interactive API docs."""
    return RedirectResponse(url="/docs")


# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health.router, tags=["health"])
app.include_router(stocks.router, prefix=settings.api_prefix, tags=["stocks"])
app.include_router(admin.router, prefix=f"{settings.api_prefix}/admin", tags=["admin"])
app.include_router(alerts.router, prefix=settings.api_prefix, tags=["alerts"])
