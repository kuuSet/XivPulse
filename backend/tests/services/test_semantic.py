"""Semantic model, matching, and recall service tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import fastembed
from app.database import init_db, make_engine
from app.models import Paper, Profile
from app.services.semantic import (
    EmbeddingModel,
    RecallService,
    SemanticMatch,
)
from app.settings import SemanticSettings
from sqlalchemy import event
from sqlalchemy.orm import Session


def test_embedding_model_loads_once_and_detects_dimensions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    instances: list[object] = []

    class FakeBackend:
        def __init__(self, **options: object) -> None:
            assert options["model_name"] == "test-embedding-model"
            assert options["threads"] == 1
            instances.append(self)

        def query_embed(self, texts: list[str], **options: object) -> list[list[float]]:
            assert options == {"batch_size": 2, "normalize": True}
            return [[1.0, 0.0] for _ in texts]

        def passage_embed(self, texts: list[str], **options: object) -> list[list[float]]:
            assert options == {"batch_size": 2, "normalize": True}
            return [[0.0, 1.0] for _ in texts]

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeBackend)
    settings = SemanticSettings(
        model_name="test-embedding-model",
        threads=1,
        batch_size=2,
    )
    model = EmbeddingModel(settings, tmp_path)

    assert model.embed_queries(["query"])[0].tolist() == [1.0, 0.0]
    assert model.embed_passages(["paper"])[0].tolist() == [0.0, 1.0]
    assert len(instances) == 1
    assert model.dimensions == 2


def test_recall_service_bulk_loads_matches_and_queues() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    statements: list[str] = []

    class FakeMatcher:
        model_name = "test-embedding-model"

        def match(
            self,
            _session: Session,
            papers: list[Paper],
            _topics: object,
        ) -> dict[int, SemanticMatch]:
            return {paper.id: SemanticMatch(0.8, {"Embodied AI": 0.8}) for paper in papers}

    with Session(engine) as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        papers = [
            Paper(
                arxiv_id=f"2608.1000{index}",
                title=f"Paper {index}",
                abstract="Robot learning.",
                authors_json=["Researcher"],
                categories_json=["cs.RO"],
                primary_category="cs.RO",
            )
            for index in range(3)
        ]
        session.add_all(papers)
        session.flush()

        def count_selects(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            if statement.lstrip().upper().startswith("SELECT") and (
                "paper_matches" in statement or "screening_queue" in statement
            ):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            result = RecallService(  # type: ignore[arg-type]
                FakeMatcher(),
                SemanticSettings(),
            ).recall(
                session,
                papers,
                profile_id=profile.id,
                topics=list(profile.topics),
                author_watchlist=[],
                report_date=date(2026, 8, 16),
                batch_size=3,
                mode="high",
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

    assert result.queued == 3
    assert len(statements) == 2
