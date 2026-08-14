"""Deterministic, explainable recommendations for a single research profile."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Analysis, Feedback, Paper, PaperMatch, Profile, Topic
from ..schemas import RecommendationRead, ScoreComponents
from .direction_matching import canonical_requested_direction_names, direction_evidence
from .direction_taxonomy import (
    direction_taxonomy_fingerprint,
    has_current_direction_attribution,
)
from .organizations import paper_organizations

POSITIVE_ACTIONS = {"like": 1.0, "save": 0.8, "read": 0.35}
NEGATIVE_ACTIONS = {"dislike": -1.0, "skip": -0.65}
NEGATIVE_REASON_SIGNALS = {
    "not_relevant": -0.95,
    "incremental": -0.45,
    "weak_evidence": -0.55,
    "no_real_robot": -0.4,
    "no_code": -0.2,
    "duplicate": -0.6,
    "poor_writing": -0.25,
    "topic_pause": -0.75,
}


@dataclass(slots=True)
class RecommendationResult:
    paper_id: int
    score: float
    explanation: str
    matched_topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    rank: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_schema(self) -> RecommendationRead:
        return RecommendationRead(
            paper_id=self.paper_id,
            score=self.score,
            rank=self.rank,
            explanation=self.explanation,
            matched_topics=self.matched_topics,
            matched_keywords=self.matched_keywords,
            components=ScoreComponents(**self.components),
        )


def _value(obj: object | Mapping[str, Any] | None, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
        return obj.get(f"{name}_json", default)
    return getattr(obj, name, getattr(obj, f"{name}_json", default))


def _list_value(obj: object | Mapping[str, Any] | None, name: str) -> list[Any]:
    value = _value(obj, name, [])
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _feedback_reason_codes(item: Feedback | Mapping[str, Any]) -> list[str]:
    codes: list[str] = []
    for reason in _list_value(item, "reasons"):
        if isinstance(reason, str):
            code = reason
        elif isinstance(reason, Mapping):
            code = str(reason.get("code", ""))
        else:
            code = str(getattr(reason, "code", ""))
        if code:
            codes.append(code)
    return codes


def _contains(text: str, phrase: str) -> bool:
    phrase = phrase.strip()
    if not phrase:
        return False
    if re.fullmatch(r"[A-Za-z0-9_+.-]+", phrase):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.I) is not None
    return phrase.casefold() in text.casefold()


def _direction_key(value: object) -> str:
    """Normalize a configured direction name without creating fuzzy aliases."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return " ".join(normalized.split()).casefold()


def _latest_analysis(
    analyses: Iterable[Analysis | Mapping[str, Any]] | None,
) -> Analysis | Mapping[str, Any] | None:
    values = list(analyses or [])
    if not values:
        return None

    def key(item: Analysis | Mapping[str, Any]) -> tuple[int, datetime]:
        kind = str(_value(item, "kind", ""))
        created = _value(item, "created_at")
        if not isinstance(created, datetime):
            created = datetime.min.replace(tzinfo=UTC)
        elif created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (1 if kind == "deep" else 0, created)

    return max(values, key=key)


def _screening_directions(
    paper: Paper | Mapping[str, Any],
    topics: Sequence[Topic | Mapping[str, Any]],
    *,
    profile_id: int | None = None,
    taxonomy_fingerprint: str,
) -> list[str]:
    completed = [
        analysis
        for analysis in _list_value(paper, "analyses")
        if str(_value(analysis, "kind", "")) == "screening"
        and str(_value(analysis, "status", "completed")) == "completed"
        and (profile_id is None or int(_value(analysis, "profile_id", 0) or 0) == profile_id)
    ]
    if not completed:
        return []

    def created_at(analysis: Analysis | Mapping[str, Any]) -> datetime:
        value = _value(analysis, "created_at")
        if not isinstance(value, datetime):
            return datetime.min.replace(tzinfo=UTC)
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    payload = _value(max(completed, key=created_at), "payload", {})
    if not isinstance(payload, Mapping):
        return []
    if not has_current_direction_attribution(payload, taxonomy_fingerprint):
        return []
    return canonical_requested_direction_names(
        payload.get("matched_topics", []),
        topics,
        relevant=payload.get("relevant") is True,
    )


def _feedback_signal(
    feedback: Iterable[Feedback | Mapping[str, Any]] | None,
    *,
    paper_topics: set[str],
    preference_by_topic: Mapping[str, float] | None,
) -> float:
    values: list[float] = []
    for item in feedback or []:
        action = str(_value(item, "action", "neutral"))
        action_value = POSITIVE_ACTIONS.get(action, NEGATIVE_ACTIONS.get(action, 0.0))
        reasons = _feedback_reason_codes(item)
        if reasons:
            reason_value = sum(NEGATIVE_REASON_SIGNALS.get(reason, 0.0) for reason in reasons)
            action_value += max(-0.7, reason_value / len(reasons))
        rating = _value(item, "rating")
        if isinstance(rating, (int, float)):
            action_value += (float(rating) - 3.0) / 4.0
        values.append(max(-1.0, min(1.0, action_value)))
    current_signal = sum(values) / len(values) if values else 0.0
    learned_signal = 0.0
    if preference_by_topic and paper_topics:
        learned_values = [
            float(preference_by_topic[_direction_key(name)])
            for name in paper_topics
            if _direction_key(name) in preference_by_topic
        ]
        if learned_values:
            learned_signal = sum(learned_values) / len(learned_values)
    return max(-1.0, min(1.0, current_signal * 0.65 + learned_signal * 0.35))


def score_paper(
    paper: Paper | Mapping[str, Any],
    profile: Profile | Mapping[str, Any] | None = None,
    topics: Sequence[Topic | Mapping[str, Any]] | None = None,
    *,
    analyses: Iterable[Analysis | Mapping[str, Any]] | None = None,
    feedback: Iterable[Feedback | Mapping[str, Any]] | None = None,
    preference_by_topic: Mapping[str, float] | None = None,
    author_watchlist: Sequence[str] | None = None,
    now: datetime | None = None,
) -> RecommendationResult:
    """Score one paper on a stable 0-100 scale with explicit components.

    The fixed weights are: topic 30, keyword 20, AI relevance 22, quality 14,
    freshness 10, watched author 8, watched institution 5, and feedback +/-4.
    Exclusion terms deduct up to 25 points; the final score is capped at 100.
    """

    del profile  # Reserved for future per-profile weight configuration.
    topics = list(topics or [])
    title = str(_value(paper, "title", ""))
    abstract = str(_value(paper, "abstract", ""))
    authors = [str(value) for value in _list_value(paper, "authors")]
    searchable = f"{title}\n{abstract}"
    categories = {str(value).casefold() for value in _list_value(paper, "categories")}

    matched_topics: list[str] = []
    matched_keywords: list[str] = []
    excluded_keywords: list[str] = []
    matched_weight = 0.0
    keyword_weight = 0.0
    max_possible_topic_weight = 0.0

    for topic in topics:
        if not bool(_value(topic, "enabled", True)):
            continue
        name = str(_value(topic, "name", "未命名主题"))
        weight = max(0.0, float(_value(topic, "weight", 1.0) or 0.0))
        max_possible_topic_weight = max(max_possible_topic_weight, weight)
        topic_categories = {
            str(value).casefold() for value in _list_value(topic, "arxiv_categories")
        }
        evidence = direction_evidence(searchable, topic)
        hits = list(evidence.keywords)
        negative_hits = list(evidence.exclusions)
        category_hit = bool(categories & topic_categories)
        if evidence.matched:
            matched_topics.append(name)
            # A category may strengthen keyword evidence, but cannot establish
            # a research-direction attribution by itself.
            matched_weight += weight * min(1.0, (0.35 if category_hit else 0.0) + 0.4 * len(hits))
            keyword_weight += weight * min(1.0, len(hits) / 2.0)
            matched_keywords.extend(hits)
        # An exclusion vetoes this direction, but should affect the paper's
        # overall score only when the same direction also had positive evidence.
        if hits:
            excluded_keywords.extend(negative_hits)

    matched_topics = list(dict.fromkeys(matched_topics))
    matched_keywords = list(dict.fromkeys(matched_keywords))
    excluded_keywords = list(dict.fromkeys(excluded_keywords))
    denominator = max(1.0, max_possible_topic_weight * 1.5)
    topic_points = 30.0 * min(1.0, matched_weight / denominator)
    keyword_points = 20.0 * min(1.0, keyword_weight / denominator)

    latest = _latest_analysis(analyses or _list_value(paper, "analyses"))
    analysis_kind = str(_value(latest, "kind", "")) if latest is not None else ""
    relevance = _value(latest, "relevance_score")
    ai_points = 22.0 * max(0.0, min(100.0, float(relevance or 0.0))) / 100.0
    quality_values = [
        float(value)
        for field_name in ("novelty_score", "rigor_score", "impact_score")
        if isinstance((value := _value(latest, field_name)), (int, float))
    ]
    quality_points = (
        14.0 * (sum(quality_values) / len(quality_values) / 100.0) if quality_values else 0.0
    )

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    published = _value(paper, "published_at") or _value(paper, "arxiv_updated_at")
    if isinstance(published, datetime):
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        age_days = max(0.0, (now - published.astimezone(UTC)).total_seconds() / 86400.0)
        freshness_points = 10.0 * math.exp(-age_days / 30.0)
    else:
        freshness_points = 3.0

    watched_authors = [
        watched
        for watched in author_watchlist or []
        if watched.strip()
        and any(watched.strip().casefold() in author.casefold() for author in authors)
    ]
    author_points = 8.0 if watched_authors else 0.0
    organizations = paper_organizations(paper)
    # Institution reputation is a visibility signal, never a substitute for
    # research-direction evidence.
    institution_points = 5.0 if organizations and matched_topics else 0.0

    topic_set = set(matched_topics)
    feedback_items = feedback if feedback is not None else _list_value(paper, "feedback_items")
    signal = _feedback_signal(
        feedback_items,
        paper_topics=topic_set,
        preference_by_topic=preference_by_topic,
    )
    feedback_points = 4.0 * signal
    exclusion_penalty = min(25.0, 12.5 * len(excluded_keywords))

    raw_score = (
        topic_points
        + keyword_points
        + ai_points
        + quality_points
        + freshness_points
        + author_points
        + institution_points
        + feedback_points
        - exclusion_penalty
    )
    score = round(max(0.0, min(100.0, raw_score)), 2)
    components = {
        "topic": round(topic_points, 2),
        "keyword": round(keyword_points, 2),
        "ai_relevance": round(ai_points, 2),
        "quality": round(quality_points, 2),
        "freshness": round(freshness_points, 2),
        "author_watch": round(author_points, 2),
        "institution_watch": round(institution_points, 2),
        "feedback": round(feedback_points, 2),
        "exclusion_penalty": round(exclusion_penalty, 2),
    }
    explanation = _explain_score(
        score,
        matched_topics,
        matched_keywords,
        excluded_keywords,
        analysis_kind,
        signal,
        watched_authors,
        organizations if institution_points else [],
    )
    return RecommendationResult(
        paper_id=int(_value(paper, "id", 0) or 0),
        score=score,
        explanation=explanation,
        matched_topics=matched_topics,
        matched_keywords=matched_keywords,
        components=components,
    )


def _explain_score(
    score: float,
    topics: list[str],
    keywords: list[str],
    excluded: list[str],
    analysis_kind: str,
    feedback_signal: float,
    watched_authors: list[str],
    organizations: list[str],
) -> str:
    parts: list[str] = []
    if topics:
        parts.append(f"匹配研究方向：{'、'.join(topics[:3])}")
    if keywords:
        parts.append(f"命中关键词：{'、'.join(keywords[:4])}")
    if analysis_kind == "deep":
        parts.append("已计入 AI 全文深读的相关度与质量评价")
    elif analysis_kind == "screening":
        parts.append("已计入 AI 摘要初筛相关度，尚无全文质量评价")
    else:
        parts.append("尚无 AI 分析，当前分数偏保守")
    if feedback_signal >= 0.2:
        parts.append("符合你的历史偏好")
    elif feedback_signal <= -0.2:
        parts.append("受历史负反馈下调")
    if watched_authors:
        parts.append(f"命中关注作者：{'、'.join(watched_authors[:3])}")
    if organizations:
        parts.append(f"重点机构：{'、'.join(organizations[:3])}")
    if excluded:
        parts.append(f"排除词惩罚：{'、'.join(excluded[:3])}")
    if not topics and not keywords:
        if watched_authors:
            parts.append("由关注作者、新鲜度和已有评价支撑")
        else:
            parts.append("仅由新鲜度和已有评价支撑")
    return f"推荐分 {score:.2f}。" + "；".join(parts) + "。"


def rank_papers(
    papers: Iterable[Paper | Mapping[str, Any]],
    profile: Profile | Mapping[str, Any] | None = None,
    topics: Sequence[Topic | Mapping[str, Any]] | None = None,
    *,
    analyses_by_paper: Mapping[int, Iterable[Analysis | Mapping[str, Any]]] | None = None,
    feedback_by_paper: Mapping[int, Iterable[Feedback | Mapping[str, Any]]] | None = None,
    preference_by_topic: Mapping[str, float] | None = None,
    author_watchlist: Sequence[str] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[RecommendationResult]:
    scored: list[tuple[RecommendationResult, datetime, str]] = []
    for paper in papers:
        paper_id = int(_value(paper, "id", 0) or 0)
        result = score_paper(
            paper,
            profile,
            topics,
            analyses=(analyses_by_paper or {}).get(paper_id),
            feedback=(feedback_by_paper or {}).get(paper_id),
            preference_by_topic=preference_by_topic,
            author_watchlist=author_watchlist,
            now=now,
        )
        published = _value(paper, "published_at")
        if not isinstance(published, datetime):
            published = datetime.min.replace(tzinfo=UTC)
        elif published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        arxiv_id = str(_value(paper, "arxiv_id", ""))
        scored.append((result, published, arxiv_id))
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    scored.sort(
        key=lambda item: (
            -item[0].score,
            -(item[1] - epoch).total_seconds(),
            item[2],
        )
    )
    results = [item[0] for item in scored[: max(0, limit) if limit is not None else None]]
    for position, result in enumerate(results, start=1):
        result.rank = position
    return results


class RecommendationEngine:
    def rank_session(
        self,
        session: Session,
        profile_id: int,
        *,
        limit: int = 20,
        author_watchlist: Sequence[str] | None = None,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> list[RecommendationResult]:
        profile = session.get(Profile, profile_id)
        if profile is None:
            raise ValueError(f"Profile {profile_id} does not exist")
        query = select(Paper).options(
            selectinload(Paper.analyses),
            selectinload(Paper.feedback_items).selectinload(Feedback.reasons),
        )
        if since is not None:
            query = query.where(Paper.last_seen_at >= since)
        papers = list(session.scalars(query).unique().all())
        papers_by_id = {paper.id: paper for paper in papers}
        preference = self._topic_preferences(session, profile)
        results = rank_papers(
            papers,
            profile,
            profile.topics,
            preference_by_topic=preference,
            author_watchlist=author_watchlist,
            now=now,
            limit=limit,
        )
        taxonomy_fingerprint = direction_taxonomy_fingerprint(profile.topics)
        for result in results:
            paper = papers_by_id[result.paper_id]
            result.matched_topics = list(
                dict.fromkeys(
                    [
                        *result.matched_topics,
                        *_screening_directions(
                            paper,
                            profile.topics,
                            profile_id=profile.id,
                            taxonomy_fingerprint=taxonomy_fingerprint,
                        ),
                    ]
                )
            )
        existing = {
            match.paper_id: match
            for match in session.scalars(
                select(PaperMatch).where(PaperMatch.profile_id == profile_id)
            ).all()
        }
        for result in results:
            match = existing.get(result.paper_id)
            if match is None:
                match = PaperMatch(paper_id=result.paper_id, profile_id=profile_id)
                session.add(match)
            match.matched_topics_json = result.matched_topics
            match.matched_keywords_json = result.matched_keywords
            match.recommendation_score = result.score
            match.reason = result.explanation
        session.flush()
        return results

    @staticmethod
    def _topic_preferences(session: Session, profile: Profile) -> dict[str, float]:
        feedback_items = session.scalars(
            select(Feedback)
            .where(Feedback.profile_id == profile.id)
            .options(
                selectinload(Feedback.paper).selectinload(Paper.analyses),
                selectinload(Feedback.reasons),
            )
        ).all()
        active_topics = [topic for topic in profile.topics if topic.enabled]
        taxonomy_fingerprint = direction_taxonomy_fingerprint(active_topics)
        totals: dict[str, list[float]] = {}
        for item in feedback_items:
            action_value = POSITIVE_ACTIONS.get(item.action, NEGATIVE_ACTIONS.get(item.action, 0.0))
            reasons = _feedback_reason_codes(item)
            if reasons:
                reason_value = sum(NEGATIVE_REASON_SIGNALS.get(reason, 0.0) for reason in reasons)
                action_value += max(-0.7, reason_value / len(reasons))
            if item.rating is not None:
                action_value += (item.rating - 3.0) / 4.0
            text = f"{item.paper.title}\n{item.paper.abstract}"
            matched_direction_names = {
                topic.name for topic in active_topics if direction_evidence(text, topic).matched
            }
            matched_direction_names.update(
                _screening_directions(
                    item.paper,
                    active_topics,
                    profile_id=profile.id,
                    taxonomy_fingerprint=taxonomy_fingerprint,
                )
            )
            bounded_value = max(-1.0, min(1.0, action_value))
            for name in matched_direction_names:
                totals.setdefault(_direction_key(name), []).append(bounded_value)
        return {name: sum(values) / len(values) for name, values in totals.items() if values}


compute_recommendation = score_paper


__all__ = [
    "RecommendationEngine",
    "RecommendationResult",
    "compute_recommendation",
    "rank_papers",
    "score_paper",
]
