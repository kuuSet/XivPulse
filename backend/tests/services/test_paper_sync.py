from __future__ import annotations

from datetime import date

import pytest
from app.bootstrap import bootstrap_application
from app.database import make_session_factory
from app.domain.paper import Paper as PaperDocument
from app.models import PaperMatch, Profile
from app.services.paper_sync import sync_papers
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.asyncio
async def test_sync_creates_unfiltered_matches_for_every_fetched_paper() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    bootstrap_application(factory)
    with factory() as session:
        profile_id = session.query(Profile).one().id

    async def fetcher(*_args: object, **_kwargs: object) -> list[PaperDocument]:
        return [
            PaperDocument("2608.01001", 1, "Paper one"),
            PaperDocument("2608.01002", 1, "Paper two"),
        ]

    result = await sync_papers(
        target_date=date(2026, 8, 18),
        session_factory=factory,
        paper_fetcher=fetcher,
    )

    assert result["fetched"] == 2
    assert result["matches_created"] == 2
    with factory() as session:
        matches = session.query(PaperMatch).order_by(PaperMatch.paper_id).all()
        assert len(matches) == 2
        assert {match.profile_id for match in matches} == {profile_id}
        assert all(match.prefiltered_at is None for match in matches)
