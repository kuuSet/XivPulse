"""JSON-ready paper views and loading options."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...models import Feedback, Paper, ProjectPaper, Topic
from ...services.directions.matching import canonical_requested_direction_names
from ...services.directions.taxonomy import has_current_direction_attribution
from ...services.organizations import paper_organizations
from ...services.state import clean_string_list as _clean_string_list
from .analyses import _analysis_wire
from .common import _canonical_directions, _direction_key, _latest_analysis
from .projects import _project_membership_wire, _reading_wire


def _paper_wire(
    paper: Paper,
    profile_id: int | None = None,
    *,
    detail: bool = False,
    topics: list[Topic] | None = None,
    taxonomy_fingerprint: str | None = None,
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
    score = match.relevance_score if match and match.relevance_score is not None else 0
    relevance = (
        analysis.relevance_score
        if analysis and analysis.relevance_score is not None
        else (match.relevance_score if match else 0)
    )
    novelty = analysis.novelty_score if analysis else None
    rigor = analysis.rigor_score if analysis else None
    priority = "high" if score >= 80 else "medium" if score >= 60 else "low"
    semantic_candidates: list[dict[str, Any]] = []
    if match is not None:
        semantic_candidates = [
            {"name": name, "score": round(float(value), 4)}
            for name, value in sorted(match.semantic_topics.items(), key=lambda item: -item[1])
        ]
    # Formal directions come only from a completed AI screening under the current
    # controlled taxonomy. Semantic and lexical signals remain visible as candidates.
    if topics is not None:
        active_topics = [topic for topic in topics if topic.enabled]
        matched_topics: list[str] = []
        direction_names = {_direction_key(topic.name): topic.name for topic in active_topics}
        screening = _latest_analysis(paper, "screening", profile_id=profile_id)
        payload = dict(screening.payload_json or {}) if screening is not None else {}
        if (
            screening is not None
            and screening.status == "completed"
            and taxonomy_fingerprint is not None
            and has_current_direction_attribution(payload, taxonomy_fingerprint)
        ):
            verified = canonical_requested_direction_names(
                payload.get("matched_topics", []),
                active_topics,
                relevant=payload.get("relevant") is True,
            )
            matched_topics = _canonical_directions(verified, direction_names)
    else:
        matched_topics = _clean_string_list(match.matched_topics if match else [], limit=20)
    matched_keywords = _clean_string_list(match.matched_keywords if match else [], limit=30)
    reasons = list(dict.fromkeys([*matched_topics, *matched_keywords]))
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
        "relevance_score": round(float(relevance or 0), 1),
        "semantic_score": round(float(match.semantic_score), 4)
        if match is not None and match.semantic_score is not None
        else None,
        "semantic_candidate_topics": semantic_candidates,
        "quality_score": rigor,
        "novelty_score": novelty,
        "priority": priority,
        "match_reasons": reasons,
        "matched_topics": matched_topics,
        "matched_keywords": matched_keywords,
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
