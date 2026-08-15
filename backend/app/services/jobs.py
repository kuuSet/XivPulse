"""Shared persistence helpers for background jobs."""

from __future__ import annotations

from typing import Any

from ..database import session_scope
from ..models import Job

RUNNING_STATUSES = ("queued", "running")


def update_job_progress(
    job_id: str | None,
    *,
    progress: int,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if job_id is None:
        return
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is not None and job.status in RUNNING_STATUSES:
            job.progress = max(0, min(100, progress))
            job.message = message
            if details:
                job.result_json = {**job.result, **details}
