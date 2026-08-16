"""Health and dashboard HTTP routes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..settings import load_runtime_settings
from ..database import get_db
from ..models import (
    Analysis,
    Digest,
    Feedback,
    Job,
    Paper,
    PaperMatch,
    Profile,
    ScreeningQueueItem,
    utcnow,
)
from ..services.direction_taxonomy import direction_taxonomy_fingerprint
from ..services.jobs import RUNNING_STATUSES
from ..services.semantic import RECALL_THRESHOLDS, recall_mode
from ..services.state import active_profile
from .presenters import _as_utc, _digest_wire, _job_wire, _paper_wire, _profile_wire, _query_papers
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
                PaperMatch.screened_at.is_not(None),
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
                PaperMatch.relevance_score >= 80,
                day_condition,
            )
        )
        or 0
    )
    papers = (
        db.scalars(
            _query_papers(db)
            .join(PaperMatch, PaperMatch.paper_id == Paper.id)
            .where(PaperMatch.profile_id == profile.id)
            .order_by(desc(PaperMatch.relevance_score), desc(Paper.published_at))
            .limit(100)
        )
        .unique()
        .all()
    )
    profile_topics = list(profile.topics)
    taxonomy_fingerprint = direction_taxonomy_fingerprint(profile_topics)
    day_papers = [
        paper
        for paper in papers
        if start <= (_as_utc(paper.published_at or paper.first_seen_at) or start) < end
    ]
    visible = day_papers or papers
    items = [
        _paper_wire(
            paper,
            profile.id,
            topics=profile_topics,
            taxonomy_fingerprint=taxonomy_fingerprint,
        )
        for paper in visible
    ]
    topics = Counter(direction for item in items for direction in item["matched_topics"])
    latest_daily_job = db.scalar(
        select(Job)
        .where(Job.kind == "daily", Job.status == "completed")
        .order_by(desc(Job.completed_at), desc(Job.created_at))
    )
    latest_result = latest_daily_job.result if latest_daily_job is not None else {}
    current_settings = load_runtime_settings(db)
    recent_cutoff = utcnow() - timedelta(days=7)
    pending_screening = (
        db.scalar(
            select(func.count(ScreeningQueueItem.id)).where(
                ScreeningQueueItem.profile_id == profile.id,
                ScreeningQueueItem.status.in_(("queued", "processing", "failed")),
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
                PaperMatch.relevance_score >= deep_threshold,
                PaperMatch.paper.has(Paper.last_seen_at >= recent_cutoff),
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
        "total_papers": fetched_today or len(visible),
        "screened_papers": screened_today,
        "high_priority_papers": high_priority_today,
        "processing_summary": {
            "latest_update_at": (
                _as_utc(latest_daily_job.completed_at) if latest_daily_job is not None else None
            ),
            "latest_fetched_count": int(latest_result.get("fetched", 0) or 0),
            "latest_screened_count": int(latest_result.get("screened", 0) or 0),
            "latest_deep_analyzed_count": int(latest_result.get("analyzed", 0) or 0),
            "pending_screening_count": pending_screening,
            "pending_deep_count": pending_deep,
            "screening_limit": current_settings.screening_batch_size,
            "screening_semantic_threshold": RECALL_THRESHOLDS[
                recall_mode(current_settings.semantic_recall_mode)
            ],
            "screening_queue_total": int(latest_result.get("screening_queue_total", 0) or 0),
            "screening_completed": int(latest_result.get("screening_completed", 0) or 0),
            "screening_failed": int(latest_result.get("screening_failed", 0) or 0),
            "screening_current_batch": int(latest_result.get("screening_batches", 0) or 0),
            "screening_total_batches": int(latest_result.get("screening_total_batches", 0) or 0),
            "report_incomplete": bool(latest_result.get("report_incomplete", False)),
            "deep_analysis_limit": current_settings.deep_analysis_limit,
            "deep_analysis_threshold": deep_threshold,
        },
        "items": items,
        "topic_stats": [{"topic": name, "count": count} for name, count in topics.most_common()],
        "profile": _profile_wire(
            profile,
            deep_threshold,
            list(current_settings.watched_authors),
        ),
        "settings": _settings_wire(db),
        "stats": {
            "fetched_today": fetched_today,
            "relevant_today": high_priority_today,
            "analyzed_today": sum(1 for paper in visible if paper.analyses),
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
                    "score": item["relevance_score"],
                    "explanation": item["why_recommended"] or "符合研究画像",
                    "matched_topics": item["match_reasons"],
                    "matched_keywords": item["match_reasons"],
                },
            }
            for item in items
        ],
        "latest_digest": _digest_wire(latest_digest),
        "active_job": _job_wire(active_job),
        "provider": _provider_public_status(db),
    }
