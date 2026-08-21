"""arXiv result parsing and persistence tests."""

from datetime import UTC, datetime

from app.arxiv.repository import PaperRepository
from app.arxiv.results import parse_arxiv_id
from app.database import init_db, make_engine
from app.domain.paper import Paper as PaperDocument
from app.models import Paper, PaperVersion
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def test_parse_dedupe_and_version_upsert() -> None:
    assert parse_arxiv_id("https://arxiv.org/pdf/2608.01234v3.pdf") == (
        "2608.01234",
        3,
    )
    records = [
        PaperDocument(
            arxiv_id="2608.01234",
            version=2,
            title="Revised title",
            abstract="Revised robot learning abstract.",
            authors=["Alice", "Bob"],
            affiliations=["OpenAI"],
            categories=["cs.RO", "cs.AI"],
            primary_category="cs.RO",
            published_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            updated_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        )
    ]

    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        first = PaperDocument(
            arxiv_id="2608.01234",
            version=1,
            title="First title",
            abstract="First abstract",
            authors=["Alice"],
            categories=["cs.RO"],
        )
        result = PaperRepository(session).upsert([first])
        session.commit()
        assert result.inserted == 1
        assert result.versions_added == 1

        result = PaperRepository(session).upsert(records)
        session.commit()
        paper = session.scalar(select(Paper).where(Paper.arxiv_id == "2608.01234"))
        assert paper is not None
        assert paper.latest_version == 2
        assert paper.title == "Revised title"
        assert result.updated == 1
        assert session.scalar(select(func.count()).select_from(Paper)) == 1
        assert session.scalar(select(func.count()).select_from(PaperVersion)) == 2

        result = PaperRepository(session).upsert(records)
        assert result.unchanged == 1
        assert result.versions_added == 0
        latest_version = session.scalar(
            select(PaperVersion).where(
                PaperVersion.paper_id == paper.id,
                PaperVersion.version == paper.latest_version,
            )
        )
        assert latest_version is not None
        latest_version.affiliations = ["NVIDIA Research"]
        assert latest_version.affiliations == ["NVIDIA Research"]
