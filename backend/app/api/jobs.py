"""Pipeline job-control and status HTTP routes."""

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Job, Paper, PaperMatch
from ..services.jobs import RUNNING_STATUSES
from ..services.paper_sync import latest_complete_submission_date
from ..services.state import active_profile
from ..settings import get_process_settings, load_runtime_settings
from ..workflow import daily_pipeline
from .presenters.jobs import _job_wire
from .system_views import _provider_public_status

router = APIRouter()

_INCOMPLETE_RESULT_KEYS = (
    "report_incomplete",
    "semantic_degraded",
    "semantic_rescore_failed",
    "screening_failed",
    "email_failed",
    "screening_errors",
    "analysis_errors",
)


class DailyJobPayload(BaseModel):
    date: date


@router.post("/api/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh() -> dict[str, Any]:
    return daily_pipeline.start_job(
        "daily", target_date=latest_complete_submission_date()
    )


@router.post("/api/run-daily", status_code=status.HTTP_202_ACCEPTED)
async def start_daily(payload: DailyJobPayload) -> dict[str, Any]:
    if payload.date > latest_complete_submission_date():
        raise HTTPException(status_code=422, detail="只能补跑已经完整结束的论文日期")
    return daily_pipeline.start_job("daily", target_date=payload.date)


@router.post("/api/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync() -> dict[str, Any]:
    return daily_pipeline.start_job(
        "sync", target_date=latest_complete_submission_date()
    )


@router.post("/api/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze() -> dict[str, Any]:
    return daily_pipeline.start_job("analyze")


@router.post("/api/semantic/rescore", status_code=status.HTTP_202_ACCEPTED)
async def semantic_rescore() -> dict[str, Any]:
    return daily_pipeline.start_job("semantic_rescore")


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
    year = int(month[:4])
    if not 1 <= year < 9999:
        raise HTTPException(status_code=422, detail="Invalid calendar month")
    jobs = db.scalars(select(Job).where(Job.kind == "daily").order_by(desc(Job.created_at))).all()
    month_number = int(month[5:])
    month_start = datetime(year, month_number, 1, tzinfo=UTC)
    month_end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if month_number == 12
        else datetime(year, month_number + 1, 1, tzinfo=UTC)
    )
    profile = active_profile(db)
    settings = load_runtime_settings(db)
    threshold = get_process_settings().semantic.threshold(settings.semantic_recall_mode)
    paper_day = func.coalesce(Paper.published_at, Paper.first_seen_at)
    rows = db.execute(
        select(
            func.date(paper_day),
            func.sum(case((PaperMatch.semantic_score >= threshold, 1), else_=0)),
            func.sum(
                case(
                    (
                        (PaperMatch.semantic_score >= threshold)
                        & PaperMatch.screened_at.is_not(None),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
        .where(
            PaperMatch.profile_id == profile.id,
            paper_day >= month_start,
            paper_day < month_end,
        )
        .group_by(func.date(paper_day))
    ).all()
    current_by_date = {
        str(day): {"candidates": int(candidates or 0), "screened": int(screened or 0)}
        for day, candidates, screened in rows
    }

    latest_by_date: dict[str, Job] = {}
    for job in jobs:
        coverage_date = job.result.get("fetch_date") or job.result.get("report_date")
        if isinstance(coverage_date, str) and coverage_date.startswith(f"{month}-"):
            latest_by_date.setdefault(coverage_date, job)

    days = []
    for day, job in sorted(latest_by_date.items()):
        result = job.result
        current = current_by_date.get(day)
        screened = (
            current["screened"] if current is not None else int(result.get("screened", 0) or 0)
        )
        candidates = (
            current["candidates"]
            if current is not None
            else int(result.get("screening_queue_total", screened) or 0)
        )
        pending_screening = max(0, candidates - screened)
        job_complete = (
            job.status == "completed"
            and not job.error
            and not any(result.get(key) for key in _INCOMPLETE_RESULT_KEYS)
        )
        complete = job_complete and pending_screening == 0
        if job_complete and pending_screening:
            message = f"有 {pending_screening} 篇当前语义候选等待 AI 初筛"
        elif complete:
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
                "candidates": candidates,
                "screened": screened,
                "pending_screening": pending_screening,
                "analyzed": int(result.get("analyzed", 0) or 0),
                "message": message,
            }
        )
    return {
        "month": month,
        "latest_available_date": latest_complete_submission_date().isoformat(),
        "days": days,
    }
