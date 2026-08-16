"""Linear data flow for one complete arXiv submission day."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..settings import load_runtime_settings
from ..database import SessionLocal, session_scope
from ..models import Topic
from .arxiv.affiliations import enrich_affiliations
from .arxiv.client import ArxivClient
from .arxiv.repository import PaperRepository
from .arxiv.semantic_scholar import SemanticScholarClient
from .paper_recall import SemanticMatcher, recall_papers
from .semantic import match_papers as semantic_match_papers
from .semantic import recall_mode
from .state import active_profile


@dataclass(frozen=True, slots=True)
class _SyncOptions:
    profile_id: int
    topics: list[Topic]
    author_watchlist: list[str]
    max_results: int
    batch_size: int
    recall_mode: str


def latest_complete_submission_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=1)


def _load_options(factory: sessionmaker[Session]) -> _SyncOptions:
    with factory() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        return _SyncOptions(
            profile_id=profile.id,
            topics=list(profile.topics),
            author_watchlist=list(settings.watched_authors),
            max_results=settings.arxiv_fetch_limit,
            batch_size=settings.screening_batch_size,
            recall_mode=recall_mode(settings.semantic_recall_mode),
        )


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
    options = _load_options(factory)
    target_date = date_provider()

    categories = [
        category
        for topic in options.topics
        if topic.enabled
        for category in topic.arxiv_categories
    ]
    papers = (
        await arxiv.fetch_daily(
            categories,
            target_date=target_date,
            max_results=options.max_results,
        )
    )[: options.max_results]

    with session_scope(factory) as db:
        stored = PaperRepository(db).upsert(papers)
        recalled = recall_papers(
            db,
            stored.papers,
            profile_id=options.profile_id,
            topics=options.topics,
            author_watchlist=options.author_watchlist,
            report_date=target_date,
            batch_size=options.batch_size,
            mode=options.recall_mode,
            matcher=semantic_matcher or semantic_match_papers,
        )

    affiliations = await enrich_affiliations(
        recalled.enrichment_ids,
        arxiv_client=arxiv,
        semantic_scholar=scholar,
    )
    if affiliations:
        with session_scope(factory) as db:
            PaperRepository(db).update_affiliations(affiliations)

    return {
        "fetched": len(papers),
        "queued": recalled.queued,
        "semantic_recall_mode": options.recall_mode,
        "semantic_degraded": recalled.semantic_error is not None,
        "semantic_error": recalled.semantic_error,
        **stored.as_dict(),
        "fetch_date": target_date.isoformat(),
        "affiliations_enriched": len(affiliations),
    }
