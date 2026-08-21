"""Dashboard HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_dashboard_reports_each_llm_stage_for_requested_date(
    client: TestClient,
    insert_paper: Callable[..., int],
) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    insert_paper(
        arxiv_id="2608.10001",
        title="Scored paper",
        published_at=selected,
        prefilter_decision="relevant",
        score=88,
    )
    insert_paper(
        arxiv_id="2608.10002",
        title="Rejected by title",
        published_at=selected,
        prefilter_decision="irrelevant",
    )
    insert_paper(
        arxiv_id="2608.10003",
        title="Waiting for title filter",
        published_at=selected,
    )
    insert_paper(
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
