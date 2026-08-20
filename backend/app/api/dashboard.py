"""Health and dashboard HTTP routes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Analysis,
    Digest,
    Feedback,
    Job,
    Paper,
    PaperMatch,
    Profile,
    utcnow,
)
from ..services.jobs import RUNNING_STATUSES
from ..services.state import active_profile
from ..settings import load_runtime_settings
from .presenters.common import _as_utc
from .presenters.interests import _interests_wire
from .presenters.jobs import _job_wire
from .presenters.papers import _paper_wire, _query_papers
from .presenters.reports import _digest_wire
from .system_views import _provider_public_status, _settings_wire

router = APIRouter()


@router.get("/api/health")
@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db.scalar(select(func.count()).select_from(Profile))
    return {
        "ok": True,
        "status": "healthy",
        "time": utcnow(),
        "provider": _provider_public_status(db),
    }


@router.get("/api/dashboard")
def dashboard(
    target_date: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    day = target_date or date.today()
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    paper_day = func.coalesce(Paper.published_at, Paper.first_seen_at)
    day_condition = (paper_day >= start) & (paper_day < end)
    current_settings = load_runtime_settings(db)
    candidate_condition = PaperMatch.screened_at.is_not(None)
    fetched_today = (
        db.scalar(
            select(func.count(Paper.id))
            .join(PaperMatch, PaperMatch.paper_id == Paper.id)
            .where(PaperMatch.profile_id == profile.id, day_condition)
        )
        or 0
    )
    screened_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screening_relevance_score.is_not(None),
                day_condition,
            )
        )
        or 0
    )
    high_priority_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screening_relevance_score >= 80,
                day_condition,
            )
        )
        or 0
    )
    deep_analyzed_today = (
        db.scalar(
            select(func.count(Analysis.id))
            .join(Paper, Paper.id == Analysis.paper_id)
            .where(
                Analysis.profile_id == profile.id,
                Analysis.kind == "deep",
                Analysis.status == "completed",
                day_condition,
            )
        )
        or 0
    )
    prefiltered_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.prefiltered_at.is_not(None),
                day_condition,
            )
        )
        or 0
    )
    prefilter_kept_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.prefilter_decision.in_(("relevant", "uncertain")),
                day_condition,
            )
        )
        or 0
    )
    effective_score = PaperMatch.effective_score_expression()
    papers = (
        db.scalars(
            _query_papers(db)
            .join(PaperMatch, PaperMatch.paper_id == Paper.id)
            .where(PaperMatch.profile_id == profile.id, candidate_condition, day_condition)
            .order_by(desc(effective_score), desc(Paper.published_at))
            .limit(100)
        )
        .unique()
        .all()
    )
    items = [_paper_wire(paper, profile.id) for paper in papers]
    topics = Counter(direction for item in items for direction in item["matched_topics"])
    daily_jobs = db.scalars(
        select(Job)
        .where(Job.kind == "daily", Job.status == "completed")
        .order_by(desc(Job.completed_at), desc(Job.created_at))
    ).all()
    latest_daily_job = daily_jobs[0] if daily_jobs and target_date is None else None
    if target_date is not None:
        for job in daily_jobs:
            job_date = job.result.get("fetch_date") or job.result.get("report_date")
            if job_date == day.isoformat():
                latest_daily_job = job
                break
    latest_result = latest_daily_job.result if latest_daily_job is not None else {}
    pending_prefilter = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.prefiltered_at.is_(None),
                day_condition,
            )
        )
        or 0
    )
    pending_screening = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.prefilter_decision.in_(("relevant", "uncertain")),
                PaperMatch.screened_at.is_(None),
                day_condition,
            )
        )
        or 0
    )
    deep_threshold = current_settings.deep_analysis_threshold
    pending_deep = (
        db.scalar(
            select(func.count(PaperMatch.id)).where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screened_at.is_not(None),
                PaperMatch.screening_relevance_score >= deep_threshold,
                PaperMatch.paper.has(day_condition),
                ~PaperMatch.paper.has(
                    Paper.analyses.any(
                        (Analysis.profile_id == profile.id)
                        & (Analysis.kind == "deep")
                        & (Analysis.status == "completed")
                    )
                ),
            )
        )
        or 0
    )
    active_job = db.scalar(
        select(Job).where(Job.status.in_(RUNNING_STATUSES)).order_by(desc(Job.created_at))
    )
    latest_digest = db.scalar(
        select(Digest).where(Digest.profile_id == profile.id).order_by(desc(Digest.digest_date))
    )
    return {
        "date": day,
        "total_papers": fetched_today or len(papers),
        "prefiltered_papers": prefiltered_today,
        "prefilter_kept_papers": prefilter_kept_today,
        "screened_papers": screened_today,
        "high_priority_papers": high_priority_today,
        "processing_summary": {
            "latest_update_at": (
                _as_utc(latest_daily_job.completed_at) if latest_daily_job is not None else None
            ),
            "latest_fetched_count": int(latest_result.get("fetched", 0) or 0),
            "latest_screened_count": screened_today,
            "latest_deep_analyzed_count": deep_analyzed_today,
            "pending_prefilter_count": pending_prefilter,
            "pending_screening_count": pending_screening,
            "pending_deep_count": pending_deep,
            "prefilter_batch_size": current_settings.prefilter_batch_size,
            "prefiltered_count": prefiltered_today,
            "prefilter_kept_count": prefilter_kept_today,
            "screened_count": screened_today,
            "ai_configured": current_settings.provider.configured,
            "deep_analysis_limit": current_settings.deep_analysis_limit,
            "deep_analysis_threshold": deep_threshold,
        },
        "items": items,
        "topic_stats": [{"topic": name, "count": count} for name, count in topics.most_common()],
        "interests": _interests_wire(profile, current_settings),
        "settings": _settings_wire(db),
        "stats": {
            "fetched_today": fetched_today,
            "relevant_today": high_priority_today,
            "analyzed_today": deep_analyzed_today,
            "saved_total": db.scalar(
                select(func.count()).select_from(Feedback).where(Feedback.action == "save")
            )
            or 0,
        },
        "recommendations": [
            {
                "paper": item,
                "recommendation": {
                    "paper_id": item["id"],
                    "score": item["effective_score"],
                    "explanation": item["why_recommended"] or "符合关注标签",
                    "matched_topics": item["match_reasons"],
                },
            }
            for item in items
        ],
        "latest_digest": _digest_wire(latest_digest),
        "active_job": _job_wire(active_job),
        "provider": _provider_public_status(db),
    }
