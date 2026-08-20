"""HTTP integration tests for the LLM-only paper flow."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app import database
from app.database import Base
from app.database_defaults import seed_defaults
from app.main import app
from app.models import Job, Paper, PaperMatch, Profile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    with database.SessionLocal() as session:
        seed_defaults(session)

    import app.pipeline as pipeline_module
    import app.runtime as runtime
    from app.workflow import daily_pipeline

    monkeypatch.setattr(runtime, "_initialized", True)
    monkeypatch.setattr(daily_pipeline, "_active_job_id", None)
    monkeypatch.setattr(
        pipeline_module,
        "PIPELINE_LOCK_PATH",
        Path.cwd() / ".pytest_pipeline.lock",
    )
    pipeline_module.PIPELINE_LOCK_PATH.unlink(missing_ok=True)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        pipeline_module.PIPELINE_LOCK_PATH.unlink(missing_ok=True)
        engine.dispose()


def _insert_paper(
    *,
    arxiv_id: str,
    title: str,
    published_at: datetime,
    prefilter_decision: str | None = None,
    score: float | None = None,
) -> int:
    with database.SessionLocal() as session:
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
                matched_topics_json=["Embodied AI"] if score is not None else [],
                reason="Relevant after abstract scoring" if score is not None else "",
                screened_at=timestamp if score is not None else None,
            )
        )
        session.commit()
        return paper.id


def test_settings_expose_only_llm_pipeline_controls(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["prefilter_batch_size"] == 30
    assert "embedding" not in payload
    assert "semantic_recall_mode" not in payload
    assert "semantic_recall_presets" not in payload

    updated = client.put("/api/settings", json={"prefilter_batch_size": 40})
    assert updated.status_code == 200
    assert updated.json()["prefilter_batch_size"] == 40
    assert client.get("/api/prompts/catalog").json()["fixed_prompts"]["prefilter"]


def test_dashboard_reports_each_llm_stage_for_requested_date(client: TestClient) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    _insert_paper(
        arxiv_id="2608.10001",
        title="Scored paper",
        published_at=selected,
        prefilter_decision="relevant",
        score=88,
    )
    _insert_paper(
        arxiv_id="2608.10002",
        title="Rejected by title",
        published_at=selected,
        prefilter_decision="irrelevant",
    )
    _insert_paper(
        arxiv_id="2608.10003",
        title="Waiting for title filter",
        published_at=selected,
    )
    _insert_paper(
        arxiv_id="2608.10004",
        title="Other day",
        published_at=datetime(2026, 8, 17, 8, tzinfo=UTC),
        prefilter_decision="relevant",
        score=99,
    )

    response = client.get("/api/dashboard?date=2026-08-18")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_papers"] == 3
    assert payload["prefiltered_papers"] == 2
    assert payload["prefilter_kept_papers"] == 1
    assert payload["screened_papers"] == 1
    assert [paper["title"] for paper in payload["items"]] == ["Scored paper"]
    assert payload["processing_summary"]["pending_prefilter_count"] == 1
    assert payload["processing_summary"]["pending_screening_count"] == 0


def test_paper_library_uses_only_abstract_llm_scores(client: TestClient) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    paper_id = _insert_paper(
        arxiv_id="2608.20001",
        title="High score",
        published_at=selected,
        prefilter_decision="uncertain",
        score=82,
    )
    _insert_paper(
        arxiv_id="2608.20002",
        title="Only prefiltered",
        published_at=selected,
        prefilter_decision="relevant",
    )

    payload = client.get("/api/papers").json()
    assert payload["total"] == 1
    assert payload["scope"] == "screened"
    assert payload["items"][0]["id"] == paper_id
    assert payload["items"][0]["effective_score"] == 82
    assert payload["items"][0]["matched_topics"] == ["Embodied AI"]
    assert "semantic_score" not in payload["items"][0]
    assert "matched_keywords" not in payload["items"][0]

    detail = client.get(f"/api/papers/{paper_id}")
    assert detail.status_code == 200


def test_job_calendar_reports_title_and_abstract_progress(client: TestClient) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    _insert_paper(
        arxiv_id="2608.30001",
        title="Scored",
        published_at=selected,
        prefilter_decision="relevant",
        score=91,
    )
    _insert_paper(
        arxiv_id="2608.30002",
        title="Waiting for abstract",
        published_at=selected,
        prefilter_decision="uncertain",
    )
    with database.SessionLocal() as session:
        session.add(
            Job(
                kind="daily",
                status="completed",
                progress=100,
                result_json={"fetch_date": "2026-08-18", "fetched": 2},
                completed_at=datetime.now(UTC),
            )
        )
        session.commit()

    payload = client.get("/api/job-calendar?month=2026-08").json()
    day = payload["days"][0]
    assert day["date"] == "2026-08-18"
    assert day["fetched"] == 2
    assert day["prefiltered"] == 2
    assert day["prefilter_kept"] == 2
    assert day["screened"] == 1
    assert day["pending_prefilter"] == 0
    assert day["pending_screening"] == 1
    assert day["status"] == "partial"


def test_removed_semantic_routes_are_not_available(client: TestClient) -> None:
    assert client.post("/api/semantic/rescore").status_code != 202
    assert client.post("/api/settings/embedding/test").status_code != 200
