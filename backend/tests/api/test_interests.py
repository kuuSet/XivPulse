"""Followed-topic API contract tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


REMOVED_TOPIC_FIELDS = {"query", "context_keywords", "negative_keywords", "weight"}


def test_interests_expose_only_active_topic_fields(client: TestClient) -> None:
    response = client.get("/api/interests")

    assert response.status_code == 200
    topics = response.json()["topics"]
    assert topics
    assert all(REMOVED_TOPIC_FIELDS.isdisjoint(topic) for topic in topics)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", "cat:cs.AI"),
        ("context_keywords", ["robot"]),
        ("negative_keywords", ["vehicle-to-load"]),
        ("weight", 2.0),
    ],
)
def test_interests_reject_removed_topic_fields(
    client: TestClient,
    field: str,
    value: object,
) -> None:
    interests = client.get("/api/interests").json()
    interests["topics"][0][field] = value

    response = client.put("/api/interests", json=interests)

    assert response.status_code == 422
