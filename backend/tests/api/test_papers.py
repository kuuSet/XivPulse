"""Paper HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_paper_library_uses_only_abstract_llm_scores(
    client: TestClient,
    insert_paper: Callable[..., int],
) -> None:
    selected = datetime(2026, 8, 18, 8, tzinfo=UTC)
    paper_id = insert_paper(
        arxiv_id="2608.20001",
        title="High score",
        published_at=selected,
        prefilter_decision="uncertain",
        score=82,
    )
    insert_paper(
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
    assert payload["items"][0]["feedback"] is None
    assert payload["items"][0]["reading"] == {
        "state": "unread",
        "priority": 3,
        "notes": "",
        "tags": [],
        "created_at": None,
        "updated_at": None,
    }
    assert payload["items"][0]["project_memberships"] == []
    assert "semantic_score" not in payload["items"][0]
    assert "matched_keywords" not in payload["items"][0]
    assert {
        "match_reasons",
        "feedback_reasons",
        "reading_state",
        "reading_priority",
        "notes",
        "tags",
        "projects",
        "screening_relevance_score",
        "analysis_tags",
        "quality_score",
        "novelty_score",
        "latest_version",
        "published_at",
        "priority",
    }.isdisjoint(payload["items"][0])

    detail = client.get(f"/api/papers/{paper_id}")
    assert detail.status_code == 200
    assert detail.json()["matched_topics"] == ["Embodied AI"]
    assert detail.json()["reading"]["state"] == "unread"


def test_feedback_uses_dismiss_and_nested_reasons(
    client: TestClient,
    insert_paper: Callable[..., int],
) -> None:
    paper_id = insert_paper(
        arxiv_id="2608.21001",
        title="Feedback paper",
        published_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        prefilter_decision="relevant",
        score=75,
    )

    response = client.post(
        f"/api/papers/{paper_id}/feedback",
        json={
            "action": "dismiss",
            "rating": 2,
            "note": "Not useful for this feed",
            "reasons": ["not_relevant", "weak_evidence"],
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "dismiss"
    detail = client.get(f"/api/papers/{paper_id}").json()
    assert detail["feedback"] == {
        "action": "dismiss",
        "note": "Not useful for this feed",
        "rating": 2,
        "reasons": ["not_relevant", "weak_evidence"],
    }
    assert "feedback_reasons" not in detail
    invalid_skip = client.post(f"/api/papers/{paper_id}/feedback", json={"action": "skip"})
    invalid_read = client.post(f"/api/papers/{paper_id}/feedback", json={"action": "read"})
    assert invalid_skip.status_code == 422
    assert invalid_read.status_code == 422


def test_reading_uses_one_nested_contract(
    client: TestClient,
    insert_paper: Callable[..., int],
) -> None:
    paper_id = insert_paper(
        arxiv_id="2608.22001",
        title="Reading paper",
        published_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        prefilter_decision="relevant",
        score=75,
    )

    response = client.patch(
        f"/api/papers/{paper_id}/reading",
        json={"state": "reading", "priority": 5, "notes": "Check experiments", "tags": ["review"]},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "reading"
    detail = client.get(f"/api/papers/{paper_id}").json()
    assert detail["reading"]["state"] == "reading"
    assert detail["reading"]["priority"] == 5
    assert detail["reading"]["notes"] == "Check experiments"
    assert detail["reading"]["tags"] == ["review"]
    assert {"reading_state", "reading_priority", "notes", "tags"}.isdisjoint(detail)
    invalid = client.patch(
        f"/api/papers/{paper_id}/reading",
        json={"reading_state": "read"},
    )
    assert invalid.status_code == 422
