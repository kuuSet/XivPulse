"""Process initialization shared by web and scheduler entrypoints."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .database import init_db, session_scope
from .models import Job, utcnow
from .services.auth import auth_settings
from .services.jobs import RUNNING_STATUSES

_initialization_lock = threading.Lock()
_initialized = False


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def initialize_application() -> None:
    global _initialized
    if _initialized:
        return
    with _initialization_lock:
        if _initialized:
            return
        auth_settings()
        init_db()
        with session_scope() as db:
            stale_before = utcnow() - timedelta(hours=2)
            for job in db.scalars(select(Job).where(Job.status.in_(RUNNING_STATUSES))).all():
                heartbeat = _as_utc(job.started_at or job.created_at)
                if heartbeat and heartbeat < stale_before:
                    job.status = "failed"
                    job.error = "应用退出后遗留的任务已自动关闭"
                    job.message = "任务超时"
                    job.completed_at = utcnow()
        _initialized = True
