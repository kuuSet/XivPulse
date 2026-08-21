"""Job HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from app.models import Job
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def test_job_status_uses_one_current_response_shape(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(Job(id="job-1", kind="sync", status="completed", result_json={"fetched": 3}))
        session.commit()

    response = client.get("/api/jobs/job-1")

    assert response.status_code == 200
    assert set(response.json()) == {
        "id",
        "kind",
        "status",
        "progress",
        "total",
        "message",
        "error",
        "result",
        "created_at",
        "started_at",
        "completed_at",
    }


def test_job_calendar_uses_fetch_date_and_database_progress(
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
        session.add_all(
            [
                Job(
                    kind="daily",
                    status="completed",
                    progress=100,
                    result_json={
                        "fetch_date": "2026-08-18",
                        "fetched": 999,
                        "prefiltered": 999,
                        "prefilter_kept": 999,
                        "screened": 999,
                    },
                    completed_at=datetime.now(UTC),
                ),
                Job(
                    kind="daily",
                    status="completed",
                    progress=100,
                    result_json={},
                    completed_at=datetime.now(UTC),
                ),
                Job(
                    kind="daily",
                    status="completed",
                    progress=100,
                    result_json={"fetch_date": "2026-08-20", "fetched": 999},
                    completed_at=datetime.now(UTC),
                ),
                Job(
                    kind="daily",
                    status="completed",
                    progress=100,
                    result_json={"fetch_date": "2026-08-21", "email_failed": 1},
                    completed_at=datetime.now(UTC),
                ),
            ]
        )
        session.commit()

    payload = client.get("/api/job-calendar?month=2026-08").json()
    assert [day["date"] for day in payload["days"]] == [
        "2026-08-18",
        "2026-08-20",
        "2026-08-21",
    ]
    day, empty_day, email_failed_day = payload["days"]
    assert day["date"] == "2026-08-18"
    assert day["fetched"] == 2
    assert day["prefiltered"] == 2
    assert day["prefilter_kept"] == 2
    assert day["screened"] == 1
    assert day["pending_prefilter"] == 0
    assert day["pending_screening"] == 1
    assert day["status"] == "partial"
    assert empty_day["fetched"] == 0
    assert empty_day["prefiltered"] == 0
    assert empty_day["prefilter_kept"] == 0
    assert empty_day["screened"] == 0
    assert empty_day["status"] == "completed"
    assert email_failed_day["status"] == "partial"
