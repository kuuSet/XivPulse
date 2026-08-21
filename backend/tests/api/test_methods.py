"""Method catalog HTTP contract tests."""

from app.models import Analysis, Paper, Profile
from app.services.methods.catalog import METHOD_CANDIDATE_MIN_PAPERS
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


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


def test_method_candidates_are_listed_and_accepted_from_one_service(
    client: TestClient,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        profile = session.query(Profile).one()
        for index in range(METHOD_CANDIDATE_MIN_PAPERS):
            paper = Paper(
                arxiv_id=f"2608.7000{index}",
                title=f"Candidate paper {index}",
                categories_json=["cs.AI"],
            )
            session.add(paper)
            session.flush()
            session.add(
                Analysis(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    kind="deep",
                    payload_json={"methods": ["Novel Test Operator"]},
                )
            )
        session.commit()

    listed = client.get("/api/methods/candidates")
    assert listed.status_code == 200
    candidate = listed.json()["items"][0]
    assert candidate["name"] == "Novel Test Operator"
    assert candidate["paper_count"] == METHOD_CANDIDATE_MIN_PAPERS

    accepted = client.post(
        "/api/methods/candidates/accept",
        json={"key": candidate["key"]},
    )
    assert accepted.status_code == 200
    assert any(
        item["name"] == "Novel Test Operator" and item["source"] == "custom"
        for item in accepted.json()["items"]
    )
