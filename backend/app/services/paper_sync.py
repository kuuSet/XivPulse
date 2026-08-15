"""Fetch, persist, and queue one complete arXiv submission day."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal, session_scope
from ..domain.paper import Paper
from ..models import Paper as StoredPaper
from ..models import PaperMatch, ScreeningQueueItem, Topic
from .arxiv.client import ArxivClient
from .arxiv.repository import PaperRepository
from .arxiv.semantic_scholar import SemanticScholarClient
from .semantic import MODEL_ID as SEMANTIC_MODEL_ID
from .semantic import RECALL_THRESHOLDS, SemanticEmbeddingError, SemanticMatch, recall_mode
from .semantic import is_candidate as is_semantic_candidate
from .semantic import match_papers as semantic_match_papers
from .state import active_profile, clean_string_list, setting_map

LOG = logging.getLogger("xivpulse")
SemanticMatcher = Callable[
    [Session, Sequence[StoredPaper], Sequence[Topic]],
    dict[int, SemanticMatch],
]


def latest_complete_submission_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=1)


def _load_options(factory: sessionmaker[Session]) -> tuple[list[Topic], int, int, str]:
    with factory() as db:
        profile = active_profile(db)
        settings = setting_map(db)
        topics = list(profile.topics)
        max_results = int(settings.get("max_results", 1000))
        screen_batch_size = int(
            settings.get("screen_batch_size", settings.get("screen_top_n", 30)) or 0
        )
        selected_recall_mode = recall_mode(settings.get("semantic_recall_mode"))
    return topics, max_results, screen_batch_size, selected_recall_mode


def _persist_and_queue(
    papers: Sequence[Paper],
    *,
    factory: sessionmaker[Session],
    semantic_matcher: SemanticMatcher,
    target_date: date,
    screen_batch_size: int,
    selected_recall_mode: str,
) -> tuple[dict[str, Any], list[str]]:
    enrichment_candidates: list[tuple[float, str]] = []
    queued_count = 0
    with session_scope(factory) as db:
        result = PaperRepository(db).upsert(papers)
        profile = active_profile(db)
        author_watchlist = clean_string_list(setting_map(db).get("author_watchlist", []))
        matches: dict[int, PaperMatch] = {}
        watched_paper_ids: set[int] = set()
        for paper in result.papers:
            match = db.scalar(
                select(PaperMatch).where(
                    PaperMatch.paper_id == paper.id,
                    PaperMatch.profile_id == profile.id,
                )
            )
            if match is None:
                match = PaperMatch(paper_id=paper.id, profile_id=profile.id)
                db.add(match)
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
        db.flush()

        semantic_error: str | None = None
        try:
            semantic_results = semantic_matcher(db, result.papers, profile.topics)
        except SemanticEmbeddingError as exc:
            semantic_results = {}
            semantic_error = str(exc)

        for paper in result.papers:
            match = matches[paper.id]
            semantic = semantic_results.get(paper.id)
            match.semantic_score = semantic.score if semantic is not None else None
            match.semantic_topics_json = semantic.topic_scores if semantic is not None else {}
            match.semantic_model = SEMANTIC_MODEL_ID if semantic is not None else None
            candidate = is_semantic_candidate(
                semantic_score=match.semantic_score,
                mode=selected_recall_mode,
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
                    and semantic.score >= RECALL_THRESHOLDS[selected_recall_mode]
                    and semantic.candidate_topics
                ):
                    match.reason = (
                        f"语义召回：{semantic.candidate_topics[0]} ({semantic.score:.3f})"
                    )
                elif paper.id in watched_paper_ids:
                    match.reason = "命中关注作者"
                elif semantic is None:
                    match.reason = "语义模型不可用"
                else:
                    match.reason = "未达到语义召回阈值"
            if match.screened_at is None and candidate:
                queue_item = db.scalar(
                    select(ScreeningQueueItem).where(
                        ScreeningQueueItem.paper_id == paper.id,
                        ScreeningQueueItem.profile_id == profile.id,
                    )
                )
                if queue_item is None:
                    queue_item = ScreeningQueueItem(
                        paper_id=paper.id,
                        profile_id=profile.id,
                        report_date=target_date,
                    )
                    db.add(queue_item)
                elif queue_item.status == "failed":
                    queue_item.status = "queued"
                    queue_item.attempts = 0
                    queue_item.last_error = None
                queue_item.recall_mode = selected_recall_mode
                queue_item.semantic_score = match.semantic_score
                queue_item.semantic_topics_json = dict(match.semantic_topics_json or {})
                queued_count += 1
                if not paper.affiliations:
                    enrichment_candidates.append((match.semantic_score or 0.0, paper.arxiv_id))

        response = {
            "fetched": len(papers),
            "queued": queued_count,
            "semantic_recall_mode": selected_recall_mode,
            "semantic_degraded": semantic_error is not None,
            "semantic_error": semantic_error,
            **result.as_dict(),
            "fetch_date": target_date.isoformat(),
        }

    selected_ids = [
        arxiv_id
        for _, arxiv_id in sorted(enrichment_candidates, reverse=True)[:screen_batch_size]
    ]
    return response, selected_ids


async def _enrich_affiliations(
    arxiv_ids: Sequence[str],
    *,
    factory: sessionmaker[Session],
    arxiv_client: ArxivClient,
    semantic_scholar: SemanticScholarClient,
) -> int:
    enriched_by_id: dict[str, list[str]] = {}
    if arxiv_ids:
        try:
            enriched_by_id.update(await semantic_scholar.fetch_affiliations(arxiv_ids))
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            LOG.warning("Semantic Scholar affiliation enrichment failed: %s", exc)
    missing_ids = [value for value in arxiv_ids if value not in enriched_by_id]
    if missing_ids:
        enriched_by_id.update(await arxiv_client.fetch_html_affiliations(missing_ids[:6]))
    if enriched_by_id:
        with session_scope(factory) as db:
            PaperRepository(db).update_affiliations(enriched_by_id)
    return len(enriched_by_id)


async def sync_papers(
    *,
    session_factory: sessionmaker[Session] | None = None,
    arxiv_client: ArxivClient | None = None,
    semantic_scholar: SemanticScholarClient | None = None,
    semantic_matcher: SemanticMatcher | None = None,
    date_provider: Callable[[], date] = latest_complete_submission_date,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    arxiv = arxiv_client or ArxivClient()
    scholar = semantic_scholar or SemanticScholarClient()
    matcher = semantic_matcher or semantic_match_papers
    topics, max_results, batch_size, selected_recall_mode = _load_options(factory)
    target_date = date_provider()
    papers = (
        await arxiv.fetch_daily(topics, target_date=target_date, max_results=max_results)
    )[:max_results]
    response, candidates = _persist_and_queue(
        papers,
        factory=factory,
        semantic_matcher=matcher,
        target_date=target_date,
        screen_batch_size=batch_size,
        selected_recall_mode=selected_recall_mode,
    )
    response["affiliations_enriched"] = await _enrich_affiliations(
        candidates,
        factory=factory,
        arxiv_client=arxiv,
        semantic_scholar=scholar,
    )
    return response
