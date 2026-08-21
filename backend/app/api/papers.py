"""Paper discovery, feedback, and reading-state HTTP routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Feedback, FeedbackReason, Paper, PaperMatch, PaperReadingState
from ..modules.papers.views import _paper_wire, _query_papers
from ..modules.projects.library import sync_favorites_membership
from ..modules.projects.tags import _clean_tags
from ..modules.projects.views import _reading_wire
from ..profile import active_profile

router = APIRouter()


class FeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["neutral", "save", "like", "dislike", "dismiss"] = "neutral"
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=5000)
    reasons: list[
        Literal[
            "not_relevant",
            "incremental",
            "weak_evidence",
            "no_real_robot",
            "no_code",
            "duplicate",
            "poor_writing",
            "topic_pause",
        ]
    ] = Field(default_factory=list, max_length=8)


ReadingStateValue = Literal["unread", "queued", "reading", "read", "reproduce", "cited", "archived"]


class ReadingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ReadingStateValue | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=50)


@router.get("/api/papers")
def papers(
    q: str | None = None,
    category: str | None = None,
    priority: Literal["high", "medium", "low"] | None = None,
    feedback: str | None = None,
    reading_state: ReadingStateValue | None = None,
    sort: Literal["recommendation", "relevance", "time"] = "recommendation",
    min_score: float = Query(default=0, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    score = PaperMatch.effective_score_expression()
    conditions = [
        PaperMatch.profile_id == profile.id,
        PaperMatch.screened_at.is_not(None),
        score >= min_score,
    ]
    if q:
        term = f"%{q.strip()}%"
        conditions.append(
            or_(
                Paper.title.ilike(term),
                Paper.abstract.ilike(term),
                Paper.arxiv_id.ilike(term),
                Paper.authors_json.contains(q.strip()),
            )
        )
    if category:
        conditions.append(Paper.categories_json.contains(category))
    if priority == "high":
        conditions.append(score >= 80)
    elif priority == "medium":
        conditions.extend((score >= 60, score < 80))
    elif priority == "low":
        conditions.append(score < 60)
    if reading_state == "unread":
        conditions.append(
            or_(
                ~Paper.reading_states.any(PaperReadingState.profile_id == profile.id),
                Paper.reading_states.any(
                    (PaperReadingState.profile_id == profile.id)
                    & (PaperReadingState.state == "unread")
                ),
            )
        )
    elif reading_state is not None:
        conditions.append(
            Paper.reading_states.any(
                (PaperReadingState.profile_id == profile.id)
                & (PaperReadingState.state == reading_state)
            )
        )

    statement = (
        _query_papers(db).join(PaperMatch, PaperMatch.paper_id == Paper.id).where(*conditions)
    )
    count_statement = (
        select(func.count(func.distinct(Paper.id)))
        .select_from(Paper)
        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
        .where(*conditions)
    )
    if feedback:
        statement = statement.join(Feedback, Feedback.paper_id == Paper.id).where(
            Feedback.profile_id == profile.id, Feedback.action == feedback
        )
        count_statement = count_statement.join(Feedback, Feedback.paper_id == Paper.id).where(
            Feedback.profile_id == profile.id, Feedback.action == feedback
        )
    if sort == "time":
        ordering = (desc(Paper.published_at), desc(score), Paper.id)
    elif sort == "relevance":
        ordering = (desc(score), desc(Paper.published_at), Paper.id)
    else:
        ordering = (desc(score), desc(Paper.published_at), Paper.id)
    statement = statement.order_by(*ordering).offset(offset).limit(limit)
    total = int(db.scalar(count_statement) or 0)
    category_rows = db.scalars(
        select(Paper.categories_json)
        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
        .where(
            PaperMatch.profile_id == profile.id,
            PaperMatch.screened_at.is_not(None),
        )
    ).all()
    available_categories = sorted(
        {value for values in category_rows for value in list(values or [])},
        key=str.casefold,
    )
    result = [_paper_wire(item, profile.id) for item in db.scalars(statement).unique().all()]
    return {
        "items": result,
        "count": len(result),
        "total": total,
        "offset": offset,
        "limit": limit,
        "page": offset // limit + 1,
        "pages": max(1, (total + limit - 1) // limit),
        "has_previous": offset > 0,
        "has_next": offset + len(result) < total,
        "scope": "screened",
        "categories": available_categories,
    }


@router.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = active_profile(db)
    paper = db.scalar(_query_papers(db).where(Paper.id == paper_id))
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    return _paper_wire(paper, profile.id, detail=True)


@router.post("/api/papers/{paper_id}/feedback")
def save_feedback(
    paper_id: int, payload: FeedbackPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    if db.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    item = db.scalar(
        select(Feedback).where(Feedback.paper_id == paper_id, Feedback.profile_id == profile.id)
    )
    if item is None:
        item = Feedback(paper_id=paper_id, profile_id=profile.id)
        db.add(item)
    item.action = payload.action
    item.rating = payload.rating
    item.note = payload.note
    requested_reasons = list(dict.fromkeys(payload.reasons))
    current_reasons = {reason.code: reason for reason in item.reasons}
    for code, reason in current_reasons.items():
        if code not in requested_reasons:
            db.delete(reason)
    for code in requested_reasons:
        if code not in current_reasons:
            item.reasons.append(FeedbackReason(code=code))
    sync_favorites_membership(db, profile.id, paper_id, saved=payload.action == "save")
    db.commit()
    return {
        "ok": True,
        "id": item.id,
        "paper_id": paper_id,
        "action": item.action,
        "note": item.note,
        "rating": item.rating,
        "reasons": requested_reasons,
    }


@router.patch("/api/papers/{paper_id}/reading")
def update_paper_reading(
    paper_id: int, payload: ReadingPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    if db.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    item = db.scalar(
        select(PaperReadingState).where(
            PaperReadingState.paper_id == paper_id,
            PaperReadingState.profile_id == profile.id,
        )
    )
    if item is None:
        item = PaperReadingState(paper_id=paper_id, profile_id=profile.id)
        db.add(item)
    if payload.state is not None:
        item.state = payload.state
    if payload.priority is not None:
        item.priority = payload.priority
    if payload.notes is not None:
        item.notes = payload.notes
    if payload.tags is not None:
        item.tags = _clean_tags(payload.tags)
    db.commit()
    db.refresh(item)
    return {"ok": True, "paper_id": paper_id, **_reading_wire(item)}
