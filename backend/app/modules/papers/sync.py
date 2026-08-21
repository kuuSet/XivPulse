"""Fetch one arXiv submission day and persist papers for LLM filtering."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ...arxiv.fetch import fetch_daily
from ...database import SessionLocal, session_scope
from ...domain.paper import Paper
from ...models import PaperMatch, Topic
from ...profile import active_profile
from ...settings import load_runtime_settings
from .repository import PaperRepository


@dataclass(frozen=True, slots=True)
class _SyncOptions:
    profile_id: int
    topics: list[Topic]
    fetch_limit: int


def latest_complete_submission_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=1)


def _load_options(factory: sessionmaker[Session]) -> _SyncOptions:
    with factory() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        return _SyncOptions(profile.id, list(profile.topics), settings.arxiv_fetch_limit)


async def sync_papers(
    *,
    target_date: date | None = None,
    session_factory: sessionmaker[Session] | None = None,
    paper_fetcher: Callable[..., Awaitable[list[Paper]]] | None = None,
    date_provider: Callable[[], date] = latest_complete_submission_date,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    options = _load_options(factory)
    target_date = target_date or date_provider()
    categories = list(
        dict.fromkeys(
            category
            for topic in options.topics
            if topic.enabled
            for category in topic.arxiv_categories
        )
    )
    papers = await (paper_fetcher or fetch_daily)(
        categories,
        target_date=target_date,
        limit=options.fetch_limit,
    )

    with session_scope(factory) as db:
        stored = PaperRepository(db).upsert(papers)
        paper_ids = [paper.id for paper in stored.papers]
        matched_ids = set(
            db.scalars(
                select(PaperMatch.paper_id).where(
                    PaperMatch.profile_id == options.profile_id,
                    PaperMatch.paper_id.in_(paper_ids),
                )
            ).all()
        )
        for paper_id in paper_ids:
            if paper_id not in matched_ids:
                db.add(PaperMatch(paper_id=paper_id, profile_id=options.profile_id))
        result = stored.as_dict()

    return {
        "fetched": len(papers),
        **result,
        "fetch_date": target_date.isoformat(),
        "matches_created": len(paper_ids) - len(matched_ids),
    }
