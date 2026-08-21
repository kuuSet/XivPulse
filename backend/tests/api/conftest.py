from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from app.models import Paper, PaperMatch, Profile
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def insert_paper(session_factory: sessionmaker[Session]) -> Callable[..., int]:
    def insert(
        *,
        arxiv_id: str,
        title: str,
        published_at: datetime,
        prefilter_decision: str | None = None,
        score: float | None = None,
    ) -> int:
        with session_factory() as session:
            profile = session.query(Profile).one()
            paper = Paper(
                arxiv_id=arxiv_id,
                title=title,
                abstract="A paper about efficient language-model systems.",
                authors_json=["Ada Author"],
                categories_json=["cs.LG"],
                primary_category="cs.LG",
                published_at=published_at,
            )
            session.add(paper)
            session.flush()
            timestamp = datetime.now(UTC)
            session.add(
                PaperMatch(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    prefilter_decision=prefilter_decision,
                    prefiltered_at=timestamp if prefilter_decision else None,
                    screening_relevance_score=score,
                    screened_at=timestamp if score is not None else None,
                )
            )
            session.commit()
            return paper.id

    return insert
