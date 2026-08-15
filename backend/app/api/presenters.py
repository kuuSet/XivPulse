"""JSON-ready views of persisted application models."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Analysis,
    Digest,
    Feedback,
    Job,
    Paper,
    PaperReadingState,
    Profile,
    ProjectPaper,
    ResearchProject,
    Topic,
)
from ..services.direction_matching import (
    canonical_requested_direction_names,
    matched_direction_names,
)
from ..services.direction_taxonomy import has_current_direction_attribution
from ..services.library import FAVORITES_LIBRARY_KIND
from ..services.organizations import paper_organizations
from ..services.state import clean_string_list as _clean_string_list


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _latest_analysis(
    paper: Paper,
    kind: str | None = None,
    *,
    profile_id: int | None = None,
) -> Analysis | None:
    analyses = [
        item
        for item in paper.analyses
        if (kind is None or item.kind == kind)
        and (profile_id is None or item.profile_id == profile_id)
    ]
    return max(
        analyses,
        key=lambda item: _as_utc(item.created_at) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )


def _direction_key(value: object) -> str:
    """Normalize exact names and aliases without semantic fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def _enabled_direction_names(profile: Profile) -> dict[str, str]:
    result: dict[str, str] = {}
    for topic in profile.topics:
        if not topic.enabled:
            continue
        for name in (topic.name, *topic.aliases):
            if key := _direction_key(name):
                result[key] = topic.name
    return result


def _canonical_directions(values: Any, names_by_key: dict[str, str]) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        key = _direction_key(value)
        canonical = names_by_key.get(key)
        if canonical is not None and key not in seen:
            seen.add(key)
            result.append(canonical)
    return result


def _configured_keyword_directions(paper: Paper, topics: list[Topic]) -> list[str]:
    """Derive direction attribution from current configured keyword evidence."""

    text = f"{paper.title}\n{paper.abstract}"
    return matched_direction_names(text, topics)


def _clean_tags(value: Any, *, limit: int = 50) -> list[str]:
    """Keep the first spelling while collapsing visually equivalent user tags."""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = unicodedata.normalize("NFKC", item).strip()
        key = re.sub(r"[\s_-]+", "", cleaned).casefold()
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _profile_wire(
    profile: Profile,
    min_relevance: float = 80.0,
    author_watchlist: list[str] | None = None,
) -> dict[str, Any]:
    enabled = [topic for topic in profile.topics if topic.enabled]
    positive = list(dict.fromkeys(keyword for topic in enabled for keyword in topic.keywords))
    negative = list(
        dict.fromkeys(keyword for topic in enabled for keyword in topic.negative_keywords)
    )
    categories = list(
        dict.fromkeys(category for topic in enabled for category in topic.arxiv_categories)
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "language": profile.language,
        "active": profile.active,
        "positive_keywords": positive,
        "negative_keywords": negative,
        "categories": categories,
        "author_watchlist": author_watchlist or [],
        "min_relevance": min_relevance,
        "topics": [
            {
                "id": topic.id,
                "profile_id": topic.profile_id,
                "name": topic.name,
                "aliases": topic.aliases,
                "query": topic.query,
                "arxiv_categories": topic.arxiv_categories,
                "keywords": topic.keywords,
                "context_keywords": topic.context_keywords,
                "negative_keywords": topic.negative_keywords,
                "weight": topic.weight,
                "enabled": topic.enabled,
                "created_at": topic.created_at,
                "updated_at": topic.updated_at,
            }
            for topic in profile.topics
        ],
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _analysis_wire(analysis: Analysis | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    payload = analysis.payload
    return {
        "id": analysis.id,
        "kind": analysis.kind,
        "status": analysis.status,
        "overview": payload.get("problem") or analysis.summary,
        "summary": analysis.summary,
        "contributions": payload.get("key_contributions", []),
        "method": "；".join(payload.get("methods", [])),
        "evidence": "；".join(payload.get("evidence", [])),
        "limitations": payload.get("limitations", analysis.weaknesses),
        "advocate": "；".join(analysis.strengths),
        "skeptic": "；".join(analysis.weaknesses),
        "input_scope": analysis.content_source,
        "budget_mode": analysis.budget_mode,
        "token_usage": {
            "available": analysis.usage_available,
            "input_tokens": analysis.input_tokens,
            "cached_input_tokens": analysis.cached_input_tokens,
            "output_tokens": analysis.output_tokens,
            "reasoning_tokens": analysis.reasoning_tokens,
            "total_tokens": analysis.total_tokens,
        },
        "relevance_score": analysis.relevance_score,
        "novelty_score": analysis.novelty_score,
        "method_score": analysis.rigor_score,
        "evidence_score": analysis.rigor_score,
        "potential_score": analysis.impact_score,
        "verdict": analysis.verdict,
        "strengths": analysis.strengths,
        "weaknesses": analysis.weaknesses,
        "tags": analysis.tags,
        "payload": payload,
        "error": analysis.error,
        "created_at": analysis.created_at,
    }


def _reading_wire(reading: PaperReadingState | None) -> dict[str, Any]:
    return {
        "state": reading.state if reading else "unread",
        "priority": reading.priority if reading else 3,
        "notes": reading.notes if reading else "",
        "tags": reading.tags if reading else [],
        "created_at": reading.created_at if reading else None,
        "updated_at": reading.updated_at if reading else None,
    }


def _project_membership_wire(link: ProjectPaper, *, include_paper: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": link.id,
        "project_id": link.project_id,
        "paper_id": link.paper_id,
        "project_name": link.project.name if link.project else None,
        "project_color": link.project.color if link.project else None,
        "reading_state": link.reading_state,
        "priority": link.priority,
        "notes": link.notes,
        "tags": link.tags,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }
    if include_paper:
        result["paper"] = {
            "id": link.paper.id,
            "arxiv_id": link.paper.arxiv_id,
            "title": link.paper.title,
            "authors": link.paper.authors,
            "primary_category": link.paper.primary_category,
            "published_at": link.paper.published_at,
        }
    return result


def _project_wire(project: ResearchProject, *, detail: bool = False) -> dict[str, Any]:
    state_counts = Counter(link.reading_state for link in project.paper_links)
    result: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "kind": project.kind,
        "is_system": project.kind == FAVORITES_LIBRARY_KIND,
        "status": project.status,
        "color": project.color,
        "goal": project.goal,
        "due_date": project.due_date,
        "paper_count": len(project.paper_links),
        "reading_counts": dict(state_counts),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if detail:
        result["papers"] = [
            _project_membership_wire(link, include_paper=True) for link in project.paper_links
        ]
    return result


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


def _job_wire(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    data = {
        "id": job.id,
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "result": job.result,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
    for key in (
        "screening_queue_total",
        "screening_completed",
        "screening_failed",
        "screening_current_batch",
        "screening_total_batches",
        "report_incomplete",
    ):
        if key in job.result:
            data[key] = job.result[key]
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


def _digest_wire(digest: Digest | None) -> dict[str, Any] | None:
    if digest is None:
        return None
    return {
        "id": digest.id,
        "profile_id": digest.profile_id,
        "digest_date": digest.digest_date,
        "title": digest.title,
        "summary": digest.summary,
        "paper_ids": digest.paper_ids,
        "stats": digest.stats,
        "status": digest.status,
        "created_at": digest.created_at,
    }
