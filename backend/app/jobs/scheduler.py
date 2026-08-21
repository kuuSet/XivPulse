"""One-per-day scheduler for Docker deployments without host cron."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..bootstrap import bootstrap_application
from ..database import SessionLocal
from ..models import Job
from ..modules.papers.sync import latest_complete_submission_date
from ..settings import load_runtime_settings
from .service import PipelineCoordinator
from .workflow import build_pipeline

LOG = logging.getLogger("xivpulse.scheduler")


def _settings(session_factory: sessionmaker[Session]) -> tuple[str, str]:
    with session_factory() as session:
        settings = load_runtime_settings(session)
    return settings.schedule_time, settings.schedule_timezone


def should_run_now(
    now: datetime | None = None,
    *,
    target_date: date | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> bool:
    schedule_value, timezone_name = _settings(session_factory)
    timezone = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(UTC)).astimezone(timezone)
    hour, minute = (int(value) for value in schedule_value.split(":", 1))
    if local_now.time().replace(tzinfo=None) < time(hour=hour, minute=minute):
        return False
    run_date = target_date or latest_complete_submission_date()
    with session_factory() as session:
        completed = session.scalar(
            select(Job.id)
            .where(
                Job.kind == "daily",
                Job.status == "completed",
                Job.result_json["fetch_date"].as_string() == run_date.isoformat(),
            )
            .limit(1)
        )
    return completed is None


async def main(
    *,
    pipeline: PipelineCoordinator | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bootstrap_application(session_factory)
    pipeline = pipeline or build_pipeline(session_factory)
    LOG.info("Docker daily scheduler started")
    while True:
        try:
            target_date = latest_complete_submission_date()
            if should_run_now(target_date=target_date, session_factory=session_factory):
                result = await pipeline.run_daily(
                    trigger="docker-scheduler",
                    target_date=target_date,
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
