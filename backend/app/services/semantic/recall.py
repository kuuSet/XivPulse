"""Semantic recall and candidate selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Paper, PaperMatch, Topic
from ...settings import RecallMode, SemanticSettings
from .matcher import SemanticMatch, SemanticMatcher
from .model import SemanticEmbeddingError


@dataclass(frozen=True, slots=True)
class RecallResult:
    candidates: int
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
    ) -> bool:
        return semantic_score is not None and semantic_score >= self.settings.threshold(mode)

    def recall(
        self,
        session: Session,
        papers: Sequence[Paper],
        *,
        profile_id: int,
        topics: Sequence[Topic],
        mode: RecallMode,
    ) -> RecallResult:
        matches = self._paper_matches(session, papers, profile_id)
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
            self._update_match(match, semantic, mode)
            if match.screened_at is None and self.is_candidate(
                match.semantic_score,
                mode,
            ):
                candidates.append((paper, match))

        enrichment_candidates: list[tuple[float, str]] = []
        for paper, match in candidates:
            if not paper.affiliations:
                enrichment_candidates.append((match.semantic_score or 0.0, paper.arxiv_id))

        enrichment_ids = [arxiv_id for _, arxiv_id in sorted(enrichment_candidates, reverse=True)]
        return RecallResult(len(candidates), enrichment_ids, semantic_error)

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
        mode: RecallMode,
    ) -> None:
        match.semantic_score = semantic.score if semantic is not None else None
        match.semantic_topics_json = semantic.topic_scores if semantic is not None else {}
        match.semantic_model = self.matcher.model_name if semantic is not None else None
        if match.screened_at is not None:
            return
        if (
            semantic is not None
            and semantic.score >= self.settings.threshold(mode)
            and semantic.candidate_topics
        ):
            match.reason = f"语义召回：{semantic.candidate_topics[0]} ({semantic.score:.3f})"
        elif semantic is None:
            match.reason = "语义模型不可用"
        else:
            match.reason = "未达到语义召回阈值"


__all__ = ["RecallResult", "RecallService"]
