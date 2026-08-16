"""One-per-day scheduler for Docker deployments without a host cron service."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import desc, select

from .settings import load_runtime_settings
from .database import SessionLocal
from .models import Job
from .runtime import initialize_application
from .workflow import daily_pipeline

LOG = logging.getLogger("xivpulse.scheduler")


def _settings() -> tuple[str, str]:
    with SessionLocal() as session:
        settings = load_runtime_settings(session)
    return settings.schedule_time, settings.timezone


def _timezone(value: str) -> ZoneInfo | timezone:
    if value.casefold() in {"utc", "etc/utc", "z"}:
        return UTC
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        if value.casefold() in {"asia/shanghai", "prc"}:
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        return UTC


def _scheduled_time(value: str) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except (AttributeError, TypeError, ValueError):
        return time(hour=13, minute=30)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def should_run_now(now: datetime | None = None) -> bool:
    schedule_value, timezone_name = _settings()
    timezone = _timezone(timezone_name)
    local_now = (now or datetime.now(UTC)).astimezone(timezone)
    if local_now.time().replace(tzinfo=None) < _scheduled_time(schedule_value):
        return False
    with SessionLocal() as session:
        latest = session.scalar(
            select(Job).where(Job.kind == "daily").order_by(desc(Job.created_at))
        )
    if latest is None:
        return True
    created_at = _as_utc(latest.created_at)
    return created_at is None or created_at.astimezone(timezone).date() < local_now.date()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    initialize_application()
    LOG.info("Docker daily scheduler started")
    while True:
        try:
            if should_run_now():
                result = await daily_pipeline.run_daily(trigger="docker-scheduler")
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
