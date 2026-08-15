"""Pipeline job-control and status HTTP routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job
from ..services.jobs import RUNNING_STATUSES
from ..workflow import daily_pipeline
from .presenters import _job_wire
from .system_views import _provider_public_status

router = APIRouter()


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
