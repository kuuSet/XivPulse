"""JSON-ready paper views and loading options."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...models import Feedback, Paper, ProjectPaper
from ...services.organizations import paper_organizations
from ...services.state import clean_string_list as _clean_string_list
from .analyses import _analysis_wire
from .common import _latest_analysis
from .projects import _project_membership_wire, _reading_wire


def _paper_wire(
    paper: Paper,
    profile_id: int | None = None,
    *,
    detail: bool = False,
) -> dict[str, Any]:
    match = next(
        (item for item in paper.matches if profile_id is None or item.profile_id == profile_id),
        None,
    )
    feedback = next(
        (
            item
            for item in paper.feedback_items
            if profile_id is None or item.profile_id == profile_id
        ),
        None,
    )
    reading = next(
        (
            item
            for item in paper.reading_states
            if profile_id is None or item.profile_id == profile_id
        ),
        None,
    )
    project_links = [
        item
        for item in paper.project_links
        if profile_id is None or item.project.profile_id == profile_id
    ]
    reading_data = _reading_wire(reading)
    analysis = _latest_analysis(paper, "deep", profile_id=profile_id) or _latest_analysis(
        paper, profile_id=profile_id
    )
    score = match.effective_score if match else 0
    novelty = analysis.novelty_score if analysis else None
    rigor = analysis.rigor_score if analysis else None
    priority = "high" if score >= 80 else "medium" if score >= 60 else "low"
    matched_topics = _clean_string_list(match.matched_topics if match else [], limit=20)
    data: dict[str, Any] = {
        "id": paper.id,
        "arxiv_id": paper.arxiv_id,
        "version": f"v{paper.latest_version}",
        "latest_version": paper.latest_version,
        "title": paper.title,
        "authors": paper.authors,
        "affiliations": paper.affiliations,
        "notable_organizations": paper_organizations(paper),
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "abstract": paper.abstract,
        "submitted_at": paper.published_at or paper.first_seen_at,
        "published_at": paper.published_at,
        "updated_at": paper.arxiv_updated_at,
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url,
        "html_url": paper.html_url,
        "screening_relevance_score": (
            round(float(match.screening_relevance_score), 1)
            if match is not None and match.screening_relevance_score is not None
            else None
        ),
        "prefilter_decision": match.prefilter_decision if match is not None else None,
        "effective_score": round(float(score), 1),
        "quality_score": rigor,
        "novelty_score": novelty,
        "priority": priority,
        "match_reasons": matched_topics,
        "matched_topics": matched_topics,
        "analysis_tags": _clean_string_list(analysis.tags if analysis else [], limit=20),
        "summary": analysis.summary if analysis else None,
        "why_recommended": match.reason if match else None,
        "feedback": {
            "action": feedback.action,
            "note": feedback.note,
            "rating": feedback.rating,
            "reasons": [reason.code for reason in feedback.reasons],
        }
        if feedback
        else None,
        "feedback_reasons": [reason.code for reason in feedback.reasons] if feedback else [],
        "reading": reading_data,
        "reading_state": reading_data["state"],
        "reading_priority": reading_data["priority"],
        "notes": reading_data["notes"],
        "tags": reading_data["tags"],
        "project_memberships": [_project_membership_wire(item) for item in project_links],
        "projects": [_project_membership_wire(item) for item in project_links],
        "analysis": _analysis_wire(analysis),
    }
    if detail:
        data["comment"] = paper.comment
        data["journal_ref"] = paper.journal_ref
        data["doi"] = paper.doi
        data["versions"] = [
            {
                "id": version.id,
                "version": version.version,
                "arxiv_version_id": version.arxiv_version_id,
                "title": version.title,
                "abstract": version.abstract,
                "authors": version.authors,
                "affiliations": version.affiliations,
                "categories": version.categories,
                "abs_url": version.abs_url,
                "html_url": version.html_url,
                "pdf_url": version.pdf_url,
                "published_at": version.published_at,
                "updated_at": version.arxiv_updated_at,
            }
            for version in paper.versions
        ]
        data["analyses"] = [
            _analysis_wire(item)
            for item in sorted(paper.analyses, key=lambda x: x.created_at, reverse=True)
        ]
    return data


def _query_papers(db: Session):
    return select(Paper).options(
        selectinload(Paper.matches),
        selectinload(Paper.analyses),
        selectinload(Paper.feedback_items).selectinload(Feedback.reasons),
        selectinload(Paper.reading_states),
        selectinload(Paper.project_links).selectinload(ProjectPaper.project),
        selectinload(Paper.versions),
    )
