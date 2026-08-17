"""Linear data flow for one complete arXiv submission day."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..arxiv.affiliations import fetch_affiliations
from ..arxiv.fetch import fetch_daily
from ..arxiv.repository import PaperRepository
from ..database import SessionLocal, session_scope
from ..domain.paper import Paper
from ..models import Topic
from ..settings import RecallMode, load_runtime_settings
from .semantic import RecallService, get_semantic_services
from .state import active_profile


@dataclass(frozen=True, slots=True)
class _SyncOptions:
    profile_id: int
    topics: list[Topic]
    fetch_limit: int
    affiliation_limit: int
    recall_mode: RecallMode


def latest_complete_submission_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=1)


def _load_options(factory: sessionmaker[Session]) -> _SyncOptions:
    with factory() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        return _SyncOptions(
            profile_id=profile.id,
            topics=list(profile.topics),
            fetch_limit=settings.arxiv_fetch_limit,
            affiliation_limit=(
                100
                if settings.watched_organizations
                else settings.screening_batch_size
            ),
            recall_mode=settings.semantic_recall_mode,
        )


async def sync_papers(
    *,
    target_date: date | None = None,
    session_factory: sessionmaker[Session] | None = None,
    paper_fetcher: Callable[..., Awaitable[list[Paper]]] | None = None,
    affiliation_fetcher: Callable[..., Awaitable[dict[str, list[str]]]] | None = None,
    recall_service: RecallService | None = None,
    date_provider: Callable[[], date] = latest_complete_submission_date,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    options = _load_options(factory)
    target_date = target_date or date_provider()

    categories = [
        category
        for topic in options.topics
        if topic.enabled
        for category in topic.arxiv_categories
    ]
    papers = await (paper_fetcher or fetch_daily)(
        categories,
        target_date=target_date,
        limit=options.fetch_limit,
    )

    with session_scope(factory) as db:
        stored = PaperRepository(db).upsert(papers)
        recalled = (recall_service or get_semantic_services().recall).recall(
            db,
            stored.papers,
            profile_id=options.profile_id,
            topics=options.topics,
            mode=options.recall_mode,
        )

    affiliations = await (affiliation_fetcher or fetch_affiliations)(
        recalled.enrichment_ids[: options.affiliation_limit]
    )
    if affiliations:
        with session_scope(factory) as db:
            PaperRepository(db).update_affiliations(affiliations)

    return {
        "fetched": len(papers),
        "semantic_candidates": recalled.candidates,
        "semantic_recall_mode": options.recall_mode,
        "semantic_degraded": recalled.semantic_error is not None,
        "semantic_error": recalled.semantic_error,
        **stored.as_dict(),
        "fetch_date": target_date.isoformat(),
        "affiliations_enriched": len(affiliations),
    }
