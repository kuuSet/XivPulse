"""Followed-topic API contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

INTEREST_FIELDS = {"topics", "watched_authors", "watched_organizations"}
TOPIC_FIELDS = {
    "aliases",
    "arxiv_categories",
    "description",
    "enabled",
    "id",
    "keywords",
    "name",
}


def test_interests_expose_only_active_topic_fields(client: TestClient) -> None:
    response = client.get("/api/interests")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == INTEREST_FIELDS
    topics = payload["topics"]
    assert topics
    assert all(set(topic) == TOPIC_FIELDS for topic in topics)


def test_interests_reject_unknown_topic_fields(client: TestClient) -> None:
    interests = client.get("/api/interests").json()
    interests["topics"][0]["unknown_field"] = True

    response = client.put("/api/interests", json=interests)

    assert response.status_code == 422
