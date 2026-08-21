"""Paper HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from app.models import Analysis, Profile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

PAPER_FIELDS = {
    "abstract",
    "abs_url",
    "affiliations",
    "analysis",
    "arxiv_id",
    "authors",
    "categories",
    "effective_score",
    "feedback",
    "html_url",
    "id",
    "matched_topics",
    "notable_organizations",
    "pdf_url",
    "prefilter_decision",
    "primary_category",
    "project_memberships",
    "reading",
    "submitted_at",
    "summary",
    "title",
    "updated_at",
    "version",
    "why_recommended",
}
DETAIL_FIELDS = {"analyses", "comment", "doi", "journal_ref", "versions"}
ANALYSIS_FIELDS = {
    "advocate",
    "budget_mode",
    "contributions",
    "evidence",
    "evidence_score",
    "input_scope",
    "kind",
    "limitations",
    "method",
    "method_score",
    "novelty_score",
    "overview",
    "potential_score",
    "relevance_score",
    "skeptic",
    "tags",
    "token_usage",
}


def test_paper_library_uses_only_abstract_llm_scores(
    client: TestClient,
    insert_paper: Callable[..., int],
    session_factory: sessionmaker[Session],
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
    with session_factory() as session:
        profile = session.query(Profile).one()
        session.add(
            Analysis(
                paper_id=paper_id,
                profile_id=profile.id,
                kind="deep",
                summary="Compact summary",
                relevance_score=82,
                payload_json={
                    "problem": "System problem",
                    "key_contributions": ["Contribution"],
                    "methods": ["Method"],
                    "evidence": ["Evidence"],
                    "limitations": ["Limitation"],
                },
            )
        )
        session.commit()

    payload = client.get("/api/papers").json()
    paper = payload["items"][0]
    assert payload["total"] == 1
    assert payload["scope"] == "screened"
    assert set(paper) == PAPER_FIELDS
    assert paper["id"] == paper_id
    assert paper["effective_score"] == 82
    assert paper["matched_topics"] == ["Embodied AI"]
    assert paper["feedback"] is None
    assert paper["reading"] == {
        "state": "unread",
        "priority": 3,
        "notes": "",
        "tags": [],
        "created_at": None,
        "updated_at": None,
    }
    assert paper["project_memberships"] == []
    assert set(paper["analysis"]) == ANALYSIS_FIELDS

    detail = client.get(f"/api/papers/{paper_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert set(detail_payload) == PAPER_FIELDS | DETAIL_FIELDS
    assert detail_payload["matched_topics"] == ["Embodied AI"]
    assert detail_payload["reading"]["state"] == "unread"
    assert all(set(analysis) == ANALYSIS_FIELDS for analysis in detail_payload["analyses"])


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
    invalid = client.post(
        f"/api/papers/{paper_id}/feedback",
        json={"action": "unsupported"},
    )
    assert invalid.status_code == 422


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
    invalid = client.patch(
        f"/api/papers/{paper_id}/reading",
        json={"unknown": "read"},
    )
    assert invalid.status_code == 422
