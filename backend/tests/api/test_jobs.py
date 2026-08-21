"""Job HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from app.models import Job
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def test_job_calendar_reports_title_and_abstract_progress(
    client: TestClient,
    session_factory: sessionmaker[Session],
    insert_paper: Callable[..., int],
) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    insert_paper(
        arxiv_id="2608.30001",
        title="Scored",
        published_at=selected,
        prefilter_decision="relevant",
        score=91,
    )
    insert_paper(
        arxiv_id="2608.30002",
        title="Waiting for abstract",
        published_at=selected,
        prefilter_decision="uncertain",
    )
    with session_factory() as session:
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
