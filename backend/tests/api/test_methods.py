"""Method catalog HTTP contract tests."""

from fastapi.testclient import TestClient


def test_custom_methods_are_validated_and_persisted(client: TestClient) -> None:
    response = client.put(
        "/api/methods/custom",
        json={"items": [{"name": "  Policy Repair  ", "aliases": [" CPR "]}]},
    )

    assert response.status_code == 200
    custom = [item for item in response.json()["items"] if item["source"] == "custom"]
    assert custom[0]["name"] == "Policy Repair"
    assert custom[0]["aliases"] == ["Policy Repair", "CPR"]
    assert client.get("/api/methods/catalog").json()["custom_count"] == 1


def test_custom_methods_reject_unknown_fields(client: TestClient) -> None:
    response = client.put(
        "/api/methods/custom",
        json={"items": [{"name": "Policy Repair", "aliases": [], "unknown": True}]},
    )

    assert response.status_code == 422
