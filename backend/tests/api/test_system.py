"""Removed HTTP route contract tests."""

from fastapi.testclient import TestClient


def test_removed_semantic_routes_are_not_available(client: TestClient) -> None:
    assert client.post("/api/semantic/rescore").status_code != 202
    assert client.post("/api/settings/embedding/test").status_code != 200
