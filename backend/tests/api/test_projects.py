"""Project HTTP contract tests."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_project_membership_round_trip_uses_canonical_paper_field(
    client: TestClient,
    insert_paper: Callable[..., int],
) -> None:
    paper_id = insert_paper(
        arxiv_id="2608.23001",
        title="Project paper",
        published_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        prefilter_decision="relevant",
        score=75,
    )
    created_project = client.post("/api/projects", json={"name": "Systems reading"})
    assert created_project.status_code == 201
    project_id = created_project.json()["id"]

    added = client.post(
        f"/api/projects/{project_id}/papers/{paper_id}",
        json={"reading_state": "queued", "priority": 4, "notes": "Reproduce", "tags": ["infra"]},
    )
    assert added.status_code == 200
    assert added.json()["reading_state"] == "queued"
    detail = client.get(f"/api/papers/{paper_id}").json()
    assert len(detail["project_memberships"]) == 1
    assert detail["project_memberships"][0]["project_id"] == project_id
    assert "projects" not in detail

    updated = client.patch(
        f"/api/projects/{project_id}/papers/{paper_id}",
        json={"reading_state": "read", "priority": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["reading_state"] == "read"
    assert updated.json()["priority"] == 5
    assert client.delete(f"/api/projects/{project_id}/papers/{paper_id}").status_code == 204
    assert client.get(f"/api/papers/{paper_id}").json()["project_memberships"] == []
