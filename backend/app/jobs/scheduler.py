"""One-per-day scheduler for Docker deployments without host cron."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal
from ..models import Job
from ..runtime import initialize_application
from ..services.paper_sync import latest_complete_submission_date
from ..settings import load_runtime_settings
from .service import PipelineCoordinator
from .workflow import build_pipeline

LOG = logging.getLogger("xivpulse.scheduler")


def _settings(session_factory: sessionmaker[Session]) -> tuple[str, str]:
    with session_factory() as session:
        settings = load_runtime_settings(session)
    return settings.schedule_time, settings.schedule_timezone


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def should_run_now(
    now: datetime | None = None,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> bool:
    schedule_value, timezone_name = _settings(session_factory)
    timezone = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(UTC)).astimezone(timezone)
    hour, minute = (int(value) for value in schedule_value.split(":", 1))
    if local_now.time().replace(tzinfo=None) < time(hour=hour, minute=minute):
        return False
    with session_factory() as session:
        latest = session.scalar(
            select(Job).where(Job.kind == "daily").order_by(desc(Job.created_at))
        )
    if latest is None:
        return True
    created_at = _as_utc(latest.created_at)
    return created_at is None or created_at.astimezone(timezone).date() < local_now.date()


async def main(
    *,
    pipeline: PipelineCoordinator | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    initialize_application(session_factory)
    pipeline = pipeline or build_pipeline(session_factory)
    LOG.info("Docker daily scheduler started")
    while True:
        try:
            if should_run_now(session_factory=session_factory):
                result = await pipeline.run_daily(
                    trigger="docker-scheduler",
                    target_date=latest_complete_submission_date(),
                )
                if result.get("accepted"):
                    LOG.info(
                        "Daily update finished: success=%s fetched=%s screened=%s analyzed=%s",
                        result.get("success"),
                        result.get("fetched", 0),
                        result.get("screened", 0),
                        result.get("analyzed", 0),
                    )
                else:
                    LOG.info("Daily update skipped because another pipeline owns the lock")
        except Exception:
            LOG.exception("Docker scheduler check failed")
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
