"""Semantic model, matching, and recall service tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import fastembed
import pytest
from app.database import init_db, make_engine, make_session_factory
from app.models import AppSetting, Paper, PaperMatch, Profile, Topic
from app.services.semantic import (
    EmbeddingModel,
    RecallService,
    SemanticMatch,
    SemanticMatcher,
    SemanticRescorer,
)
from app.settings import SemanticSettings
from sqlalchemy import event, select
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


def test_semantic_fingerprint_tracks_only_score_inputs() -> None:
    model = type("Model", (), {"model_name": "test-model"})()
    matcher = SemanticMatcher(model)  # type: ignore[arg-type]
    first = Topic(
        profile_id=1,
        name="Embodied AI",
        aliases_json=["Embodied intelligence"],
        keywords_json=["robot learning"],
        context_keywords_json=["physical world"],
        enabled=True,
    )
    second = Topic(profile_id=1, name="World models", enabled=False)

    initial = matcher.fingerprint([first, second])
    first.keywords = ["vision-language-action"]

    assert matcher.fingerprint([first, second]) != initial
    changed = matcher.fingerprint([first, second])
    second.keywords = ["unused disabled direction"]
    assert matcher.fingerprint([first, second]) == changed
    assert matcher.fingerprint([first, second]) == matcher.fingerprint([second, first])


def test_rescorer_skips_unchanged_fingerprint_and_force_uses_keyset_batches() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    paper_queries: list[str] = []
    progress: list[tuple[int, int]] = []

    class FakeMatcher:
        model_name = "test-model"
        calls = 0

        def fingerprint(self, _topics: object) -> str:
            return "current-fingerprint"

        def match(
            self,
            _session: Session,
            papers: list[Paper],
            _topics: object,
        ) -> dict[int, SemanticMatch]:
            self.calls += 1
            return {paper.id: SemanticMatch(0.75, {"Embodied AI": 0.75}) for paper in papers}

    matcher = FakeMatcher()
    with factory() as db:
        profile = db.query(Profile).filter(Profile.active.is_(True)).one()
        for index in range(5):
            paper = Paper(
                arxiv_id=f"2608.2000{index}",
                title=f"Paper {index}",
                abstract="Robot learning.",
            )
            db.add(paper)
            db.flush()
            db.add(PaperMatch(paper_id=paper.id, profile_id=profile.id))
        db.add(
            AppSetting(
                key="semantic_score_fingerprint",
                value_json="current-fingerprint",
            )
        )
        db.add(
            AppSetting(
                key="semantic_rescore_status",
                value_json={
                    "status": "completed",
                    "fingerprint": "current-fingerprint",
                    "processed": 5,
                    "total": 5,
                },
            )
        )
        db.commit()

    def record_paper_queries(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "FROM papers" in statement:
            paper_queries.append(statement)

    rescorer = SemanticRescorer(  # type: ignore[arg-type]
        matcher,
        session_factory=factory,
        batch_size=2,
    )
    event.listen(engine, "before_cursor_execute", record_paper_queries)
    try:
        skipped = rescorer.rescore()
        assert skipped["skipped"] is True
        assert paper_queries == []

        completed = rescorer.rescore(
            force=True,
            progress=lambda done, total: progress.append((done, total)),
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_paper_queries)

    assert completed == {
        "status": "completed",
        "processed": 5,
        "total": 5,
        "forced": True,
        "skipped": False,
    }
    assert matcher.calls == 3
    assert progress == [(2, 5), (4, 5), (5, 5)]
    with factory() as db:
        scores = list(db.scalars(select(PaperMatch.semantic_score)).all())
        assert scores == [0.75] * 5


def test_rescorer_failure_keeps_previous_fingerprint() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)

    class FailingMatcher:
        model_name = "test-model"

        def fingerprint(self, _topics: object) -> str:
            return "new-fingerprint"

        def match(self, *_values: object) -> dict[int, SemanticMatch]:
            raise RuntimeError("embedding unavailable")

    with factory() as db:
        profile = db.query(Profile).filter(Profile.active.is_(True)).one()
        paper = Paper(arxiv_id="2608.30001", title="Paper", abstract="Abstract")
        db.add(paper)
        db.flush()
        db.add(PaperMatch(paper_id=paper.id, profile_id=profile.id))
        db.add(AppSetting(key="semantic_score_fingerprint", value_json="old-fingerprint"))
        db.commit()

    rescorer = SemanticRescorer(  # type: ignore[arg-type]
        FailingMatcher(),
        session_factory=factory,
    )
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        rescorer.rescore()

    with factory() as db:
        assert db.get(AppSetting, "semantic_score_fingerprint").value_json == "old-fingerprint"
        status = db.get(AppSetting, "semantic_rescore_status").value_json
        assert status["status"] == "failed"
        assert status["processed"] == 0
        assert rescorer.status(db)["status"] == "failed"


def test_rescorer_leaves_pending_when_directions_change_during_work() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)

    class ChangingMatcher:
        model_name = "test-model"
        fingerprints = iter(("started-fingerprint", "changed-fingerprint"))

        def fingerprint(self, _topics: object) -> str:
            return next(self.fingerprints)

        def match(
            self,
            _session: Session,
            papers: list[Paper],
            _topics: object,
        ) -> dict[int, SemanticMatch]:
            return {paper.id: SemanticMatch(0.5, {"Topic": 0.5}) for paper in papers}

    with factory() as db:
        profile = db.query(Profile).filter(Profile.active.is_(True)).one()
        paper = Paper(arxiv_id="2608.30002", title="Paper", abstract="Abstract")
        db.add(paper)
        db.flush()
        db.add(PaperMatch(paper_id=paper.id, profile_id=profile.id))
        db.add(AppSetting(key="semantic_score_fingerprint", value_json="previous-fingerprint"))
        db.commit()

    result = SemanticRescorer(  # type: ignore[arg-type]
        ChangingMatcher(),
        session_factory=factory,
    ).rescore()

    assert result["status"] == "pending"
    with factory() as db:
        assert db.get(AppSetting, "semantic_score_fingerprint").value_json == "previous-fingerprint"
        assert db.get(AppSetting, "semantic_rescore_status").value_json == {"status": "pending"}
