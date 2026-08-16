"""Pipeline job-control and status HTTP routes."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job
from ..settings import load_runtime_settings
from ..services.jobs import RUNNING_STATUSES
from ..workflow import daily_pipeline
from .presenters import _job_wire
from .system_views import _provider_public_status

router = APIRouter()

_INCOMPLETE_RESULT_KEYS = (
    "report_incomplete",
    "semantic_degraded",
    "screening_failed",
    "email_failed",
    "screening_errors",
    "analysis_errors",
)


@router.post("/api/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh() -> dict[str, Any]:
    return daily_pipeline.start_job("daily")


@router.post("/api/run-daily", status_code=status.HTTP_202_ACCEPTED)
async def start_daily() -> dict[str, Any]:
    return daily_pipeline.start_job("daily")


@router.post("/api/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync() -> dict[str, Any]:
    return daily_pipeline.start_job("sync")


@router.post("/api/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze() -> dict[str, Any]:
    return daily_pipeline.start_job("analyze")


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_wire(job) or {}


@router.get("/api/status")
def status_view(db: Session = Depends(get_db)) -> dict[str, Any]:
    active = db.scalar(
        select(Job).where(Job.status.in_(RUNNING_STATUSES)).order_by(desc(Job.created_at))
    )
    latest = db.scalar(select(Job).order_by(desc(Job.created_at)))
    return {
        "busy": active is not None,
        "active_job": _job_wire(active),
        "latest_job": _job_wire(latest),
        "provider": _provider_public_status(db),
    }


@router.get("/api/job-calendar")
def job_calendar(
    month: str = Query(pattern=r"^\d{4}-(?:0[1-9]|1[0-2])$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    year, month_number = map(int, month.split("-"))
    if not 1 <= year < 9999:
        raise HTTPException(status_code=422, detail="Invalid calendar month")
    next_year, next_month = (
        (year + 1, 1) if month_number == 12 else (year, month_number + 1)
    )
    timezone = ZoneInfo(load_runtime_settings(db).schedule_timezone)
    month_start = datetime(year, month_number, 1, tzinfo=timezone)
    month_end = datetime(next_year, next_month, 1, tzinfo=timezone)
    jobs = db.scalars(
        select(Job)
        .where(
            Job.kind == "daily",
            Job.created_at >= month_start.astimezone(UTC),
            Job.created_at < month_end.astimezone(UTC),
        )
        .order_by(desc(Job.created_at))
    ).all()

    latest_by_date: dict[str, Job] = {}
    for job in jobs:
        created_at = job.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        latest_by_date.setdefault(created_at.astimezone(timezone).date().isoformat(), job)

    days = []
    for day, job in sorted(latest_by_date.items()):
        result = job.result
        complete = (
            job.status == "completed"
            and not job.error
            and not any(result.get(key) for key in _INCOMPLETE_RESULT_KEYS)
        )
        if complete:
            message = "任务完整完成"
        elif job.status in RUNNING_STATUSES:
            message = job.message or "任务进行中"
        else:
            message = job.error or job.message or "任务未完整完成"
        days.append(
            {
                "date": day,
                "status": "completed" if complete else "partial",
                "fetched": int(result.get("fetched", 0) or 0),
                "screened": int(result.get("screened", 0) or 0),
                "analyzed": int(result.get("analyzed", 0) or 0),
                "message": message,
            }
        )
    return {"month": month, "days": days}
