"""Semantic recall policy and screening queue persistence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Paper, PaperMatch, ScreeningQueueItem, Topic
from ...settings import RecallMode, SemanticSettings
from .matcher import SemanticMatch, SemanticMatcher
from .model import SemanticEmbeddingError


@dataclass(frozen=True, slots=True)
class RecallResult:
    queued: int
    enrichment_ids: list[str]
    semantic_error: str | None


class RecallService:
    def __init__(self, matcher: SemanticMatcher, settings: SemanticSettings) -> None:
        self.matcher = matcher
        self.settings = settings

    def is_candidate(
        self,
        semantic_score: float | None,
        mode: RecallMode,
        *,
        watched_author: bool = False,
    ) -> bool:
        return watched_author or (
            semantic_score is not None and semantic_score >= self.settings.threshold(mode)
        )

    def recall(
        self,
        session: Session,
        papers: Sequence[Paper],
        *,
        profile_id: int,
        topics: Sequence[Topic],
        author_watchlist: Sequence[str],
        report_date: date,
        batch_size: int,
        mode: RecallMode,
    ) -> RecallResult:
        matches = self._paper_matches(session, papers, profile_id)
        watched_ids = self._watched_paper_ids(papers, author_watchlist)
        for match in matches.values():
            if match.screened_at is None:
                match.matched_topics_json = []
                match.matched_keywords_json = []
                match.excluded_keywords_json = []
                match.lexical_score = 0.0
        session.flush()

        semantic_error: str | None = None
        try:
            semantic_results = self.matcher.match(session, papers, topics)
        except SemanticEmbeddingError as exc:
            semantic_results = {}
            semantic_error = str(exc)

        candidates: list[tuple[Paper, PaperMatch]] = []
        for paper in papers:
            match = matches[paper.id]
            semantic = semantic_results.get(paper.id)
            self._update_match(match, semantic, paper.id in watched_ids, mode)
            if match.screened_at is None and self.is_candidate(
                match.semantic_score,
                mode,
                watched_author=paper.id in watched_ids,
            ):
                candidates.append((paper, match))

        queues = self._queue_items(session, candidates, profile_id)
        enrichment_candidates: list[tuple[float, str]] = []
        for paper, match in candidates:
            queue = queues.get(paper.id)
            if queue is None:
                queue = ScreeningQueueItem(
                    paper_id=paper.id,
                    profile_id=profile_id,
                    report_date=report_date,
                )
                session.add(queue)
            elif queue.status == "failed":
                queue.status = "queued"
                queue.attempts = 0
                queue.last_error = None
            queue.recall_mode = mode
            queue.semantic_score = match.semantic_score
            queue.semantic_topics_json = dict(match.semantic_topics_json or {})
            if not paper.affiliations:
                enrichment_candidates.append((match.semantic_score or 0.0, paper.arxiv_id))

        enrichment_ids = [
            arxiv_id for _, arxiv_id in sorted(enrichment_candidates, reverse=True)[:batch_size]
        ]
        return RecallResult(len(candidates), enrichment_ids, semantic_error)

    @staticmethod
    def _watched_paper_ids(
        papers: Sequence[Paper],
        author_watchlist: Sequence[str],
    ) -> set[int]:
        watched = [value.casefold() for value in author_watchlist if value.strip()]
        return {
            paper.id
            for paper in papers
            if any(name in author.casefold() for name in watched for author in paper.authors)
        }

    @staticmethod
    def _queue_items(
        session: Session,
        candidates: Sequence[tuple[Paper, PaperMatch]],
        profile_id: int,
    ) -> dict[int, ScreeningQueueItem]:
        paper_ids = [paper.id for paper, _ in candidates]
        if not paper_ids:
            return {}
        return {
            item.paper_id: item
            for item in session.scalars(
                select(ScreeningQueueItem).where(
                    ScreeningQueueItem.profile_id == profile_id,
                    ScreeningQueueItem.paper_id.in_(paper_ids),
                )
            ).all()
        }

    def _paper_matches(
        self,
        session: Session,
        papers: Sequence[Paper],
        profile_id: int,
    ) -> dict[int, PaperMatch]:
        paper_ids = [paper.id for paper in papers]
        matches = {
            match.paper_id: match
            for match in session.scalars(
                select(PaperMatch).where(
                    PaperMatch.profile_id == profile_id,
                    PaperMatch.paper_id.in_(paper_ids),
                )
            ).all()
        }
        for paper_id in paper_ids:
            if paper_id not in matches:
                match = PaperMatch(paper_id=paper_id, profile_id=profile_id)
                session.add(match)
                matches[paper_id] = match
        return matches

    def _update_match(
        self,
        match: PaperMatch,
        semantic: SemanticMatch | None,
        watched_author: bool,
        mode: RecallMode,
    ) -> None:
        match.semantic_score = semantic.score if semantic is not None else None
        match.semantic_topics_json = semantic.topic_scores if semantic is not None else {}
        match.semantic_model = self.matcher.model_name if semantic is not None else None
        if match.screened_at is not None:
            return
        match.relevance_score = (
            round(100 * max(0.0, min(1.0, semantic.score)), 2) if semantic is not None else 0
        )
        if (
            semantic is not None
            and semantic.score >= self.settings.threshold(mode)
            and semantic.candidate_topics
        ):
            match.reason = f"语义召回：{semantic.candidate_topics[0]} ({semantic.score:.3f})"
        elif watched_author:
            match.reason = "命中关注作者"
        elif semantic is None:
            match.reason = "语义模型不可用"
        else:
            match.reason = "未达到语义召回阈值"


__all__ = ["RecallResult", "RecallService"]
