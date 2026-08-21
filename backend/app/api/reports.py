"""Trend and daily-report HTTP routes."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DailyResearchReport
from ..modules.reports.service import (
    persist_daily_report,
    report_snapshot,
    send_daily_report_email,
    synthesize_daily_report,
)
from ..profile import active_profile
from ..settings import load_runtime_settings
from .presenters.common import _as_utc

router = APIRouter()


@router.get("/api/reports/daily/history")
def daily_research_report_history(
    limit: int = Query(default=60, ge=1, le=3650),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    snapshots = db.scalars(
        select(DailyResearchReport)
        .where(DailyResearchReport.profile_id == profile.id)
        .order_by(desc(DailyResearchReport.report_date))
        .limit(limit)
    ).all()
    items = [
        {
            "id": snapshot.id,
            "report_date": snapshot.report_date,
            "title": snapshot.payload.get("title", snapshot.report_date.isoformat()),
            "coverage": snapshot.payload.get("coverage", {}),
            "generated_at": _as_utc(snapshot.created_at),
            "updated_at": _as_utc(snapshot.updated_at),
        }
        for snapshot in snapshots
    ]
    return {"items": items, "count": len(items)}


def _get_or_refresh_daily_research_report(
    db: Session,
    *,
    report_date: date | None,
    max_papers: int,
    refresh: bool,
) -> dict[str, Any]:
    profile = active_profile(db)
    timezone_name = load_runtime_settings(db).schedule_timezone
    selected_date = report_date
    if selected_date is None:
        selected_date = db.scalar(
            select(DailyResearchReport.report_date)
            .where(DailyResearchReport.profile_id == profile.id)
            .order_by(desc(DailyResearchReport.report_date))
            .limit(1)
        )
    snapshot = persist_daily_report(
        db,
        profile.id,
        report_date=selected_date,
        timezone_name=timezone_name,
        max_papers=max_papers,
        refresh=refresh,
    )
    db.commit()
    db.refresh(snapshot)
    return report_snapshot(snapshot)


@router.get("/api/reports/daily")
def daily_research_report(
    report_date: date | None = Query(default=None, alias="date"),
    max_papers: int = Query(default=10, ge=1, le=30),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _get_or_refresh_daily_research_report(
        db,
        report_date=report_date,
        max_papers=max_papers,
        refresh=refresh,
    )


@router.post("/api/reports/daily/regenerate")
def regenerate_daily_research_report(
    report_date: date | None = Query(default=None, alias="date"),
    max_papers: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _get_or_refresh_daily_research_report(
        db,
        report_date=report_date,
        max_papers=max_papers,
        refresh=True,
    )


@router.post("/api/reports/daily/synthesize")
async def synthesize_daily_research_report(
    request: Request,
    report_date: date = Query(alias="date"),
    force: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    await synthesize_daily_report(
        report_date,
        force=force,
        respect_enabled=False,
        session_factory=request.app.state.session_factory,
    )
    profile = active_profile(db)
    snapshot = db.scalar(
        select(DailyResearchReport).where(
            DailyResearchReport.profile_id == profile.id,
            DailyResearchReport.report_date == report_date,
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="研究日报不存在")
    return report_snapshot(snapshot)


@router.post("/api/reports/daily/send")
async def send_daily_research_report(
    request: Request,
    report_date: date = Query(alias="date"),
) -> dict[str, Any]:
    result = await send_daily_report_email(
        report_date,
        delivery_kind=f"manual-{uuid4().hex[:10]}",
        require_enabled=False,
        session_factory=request.app.state.session_factory,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}
