"""Digest, trend, and daily-report HTTP routes."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..settings import load_runtime_settings
from ..database import SessionLocal, get_db
from ..models import DailyResearchReport, Digest, Paper, PaperMatch
from ..services.analytics import build_research_roadmap, build_topic_trends
from ..services.daily_reports import (
    persist_daily_report,
    report_snapshot,
    send_daily_report_email,
    synthesize_daily_report,
)
from ..services.direction_taxonomy import direction_taxonomy_fingerprint
from ..services.state import active_profile
from .presenters.common import _as_utc
from .presenters.papers import _paper_wire, _query_papers
from .presenters.reports import _digest_wire

router = APIRouter()


@router.get("/api/digest/preview")
def digest_preview(
    target_date: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    day = target_date or date.today()
    digest = db.scalar(
        select(Digest).where(Digest.profile_id == profile.id, Digest.digest_date == day)
    )
    if digest is None:
        papers = (
            db.scalars(
                _query_papers(db)
                .join(PaperMatch, PaperMatch.paper_id == Paper.id)
                .where(PaperMatch.profile_id == profile.id)
                .order_by(desc(PaperMatch.relevance_score))
                .limit(10)
            )
            .unique()
            .all()
        )
        return {
            "digest_date": day,
            "title": f"{day.isoformat()} arXiv 每日精选（预览）",
            "summary": f"当前有 {len(papers)} 篇候选论文。",
            "items": [
                _paper_wire(
                    paper,
                    profile.id,
                    topics=list(profile.topics),
                    taxonomy_fingerprint=direction_taxonomy_fingerprint(list(profile.topics)),
                )
                for paper in papers
            ],
            "persisted": False,
        }
    paper_ids = digest.paper_ids
    papers = (
        db.scalars(_query_papers(db).where(Paper.id.in_(paper_ids))).unique().all()
        if paper_ids
        else []
    )
    by_id = {paper.id: paper for paper in papers}
    profile_topics = list(profile.topics)
    taxonomy_fingerprint = direction_taxonomy_fingerprint(profile_topics)
    return {
        **(_digest_wire(digest) or {}),
        "items": [
            _paper_wire(
                by_id[item],
                profile.id,
                topics=profile_topics,
                taxonomy_fingerprint=taxonomy_fingerprint,
            )
            for item in paper_ids
            if item in by_id
        ],
        "persisted": True,
    }


@router.get("/api/trends/topics")
def topic_trends(
    as_of: date | None = Query(default=None, alias="date"),
    limit: int = Query(default=12, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    timezone_name = load_runtime_settings(db).schedule_timezone
    return build_topic_trends(
        db,
        profile.id,
        as_of=as_of,
        timezone_name=timezone_name,
        limit=limit,
    )


@router.get("/api/trends/roadmap")
def research_roadmap(
    as_of: date | None = Query(default=None, alias="date"),
    days: int = Query(default=90, ge=7, le=730),
    max_nodes: int = Query(default=30, ge=5, le=60),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    timezone_name = load_runtime_settings(db).schedule_timezone
    return build_research_roadmap(
        db,
        profile.id,
        as_of=as_of,
        timezone_name=timezone_name,
        days=days,
        max_nodes=max_nodes,
    )


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
            select(Digest.digest_date)
            .where(Digest.profile_id == profile.id)
            .order_by(desc(Digest.digest_date))
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
    report_date: date = Query(alias="date"),
    force: bool = Query(default=True),
) -> dict[str, Any]:
    await synthesize_daily_report(
        report_date,
        force=force,
        respect_enabled=False,
    )
    with SessionLocal() as db:
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
    report_date: date = Query(alias="date"),
) -> dict[str, Any]:
    result = await send_daily_report_email(
        report_date,
        delivery_kind=f"manual-{uuid4().hex[:10]}",
        require_enabled=False,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}
