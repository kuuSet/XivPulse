"""Semantic recall and screening queue selection for stored papers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper, PaperMatch, ScreeningQueueItem, Topic
from .semantic import (
    MODEL_ID,
    RECALL_THRESHOLDS,
    SemanticEmbeddingError,
    SemanticMatch,
    is_candidate,
)

SemanticMatcher = Callable[
    [Session, Sequence[Paper], Sequence[Topic]],
    dict[int, SemanticMatch],
]


@dataclass(frozen=True, slots=True)
class RecallResult:
    queued: int
    enrichment_ids: list[str]
    semantic_error: str | None


def recall_papers(
    session: Session,
    papers: Sequence[Paper],
    *,
    profile_id: int,
    topics: Sequence[Topic],
    author_watchlist: Sequence[str],
    report_date: date,
    batch_size: int,
    mode: str,
    matcher: SemanticMatcher,
) -> RecallResult:
    matches: dict[int, PaperMatch] = {}
    watched_paper_ids: set[int] = set()
    for paper in papers:
        match = session.scalar(
            select(PaperMatch).where(
                PaperMatch.paper_id == paper.id,
                PaperMatch.profile_id == profile_id,
            )
        )
        if match is None:
            match = PaperMatch(paper_id=paper.id, profile_id=profile_id)
            session.add(match)
        matches[paper.id] = match
        if any(
            watched.casefold() in author.casefold()
            for watched in author_watchlist
            for author in paper.authors
        ):
            watched_paper_ids.add(paper.id)
        if match.screened_at is None:
            match.matched_topics_json = []
            match.matched_keywords_json = []
            match.excluded_keywords_json = []
            match.lexical_score = 0.0
    session.flush()

    semantic_error: str | None = None
    try:
        semantic_results = matcher(session, papers, topics)
    except SemanticEmbeddingError as exc:
        semantic_results = {}
        semantic_error = str(exc)

    queued = 0
    enrichment_candidates: list[tuple[float, str]] = []
    for paper in papers:
        match = matches[paper.id]
        semantic = semantic_results.get(paper.id)
        match.semantic_score = semantic.score if semantic is not None else None
        match.semantic_topics_json = semantic.topic_scores if semantic is not None else {}
        match.semantic_model = MODEL_ID if semantic is not None else None
        candidate = is_candidate(
            semantic_score=match.semantic_score,
            mode=mode,
            watched_author=paper.id in watched_paper_ids,
        )
        if match.screened_at is None:
            match.relevance_score = (
                round(100 * max(0.0, min(1.0, semantic.score)), 2)
                if semantic is not None
                else 0
            )
            if (
                semantic is not None
                and semantic.score >= RECALL_THRESHOLDS[mode]
                and semantic.candidate_topics
            ):
                match.reason = f"语义召回：{semantic.candidate_topics[0]} ({semantic.score:.3f})"
            elif paper.id in watched_paper_ids:
                match.reason = "命中关注作者"
            elif semantic is None:
                match.reason = "语义模型不可用"
            else:
                match.reason = "未达到语义召回阈值"
        if match.screened_at is None and candidate:
            queue_item = session.scalar(
                select(ScreeningQueueItem).where(
                    ScreeningQueueItem.paper_id == paper.id,
                    ScreeningQueueItem.profile_id == profile_id,
                )
            )
            if queue_item is None:
                queue_item = ScreeningQueueItem(
                    paper_id=paper.id,
                    profile_id=profile_id,
                    report_date=report_date,
                )
                session.add(queue_item)
            elif queue_item.status == "failed":
                queue_item.status = "queued"
                queue_item.attempts = 0
                queue_item.last_error = None
            queue_item.recall_mode = mode
            queue_item.semantic_score = match.semantic_score
            queue_item.semantic_topics_json = dict(match.semantic_topics_json or {})
            queued += 1
            if not paper.affiliations:
                enrichment_candidates.append((match.semantic_score or 0.0, paper.arxiv_id))

    enrichment_ids = [
        arxiv_id
        for _, arxiv_id in sorted(enrichment_candidates, reverse=True)[:batch_size]
    ]
    return RecallResult(queued, enrichment_ids, semantic_error)
