"""Daily-report HTTP contract tests."""

from __future__ import annotations

from datetime import date

from app.models import DailyResearchReport, Profile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def _insert_report(session_factory: sessionmaker[Session], report_date: date, title: str) -> None:
    with session_factory() as session:
        profile = session.query(Profile).one()
        session.add(
            DailyResearchReport(
                profile_id=profile.id,
                report_date=report_date,
                payload_json={
                    "report_date": report_date.isoformat(),
                    "title": title,
                    "coverage": {},
                },
            )
        )
        session.commit()


def test_latest_saved_report_drives_dashboard_and_default_report(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    _insert_report(session_factory, date(2026, 8, 12), "Earlier report")
    _insert_report(session_factory, date(2026, 8, 13), "Latest report")

    dashboard = client.get("/api/dashboard").json()
    response = client.get("/api/reports/daily")

    assert dashboard["latest_report_date"] == "2026-08-13"
    assert dashboard["latest_completed_date"] is None
    assert "latest_digest" not in dashboard
    assert response.status_code == 200
    assert response.json()["report_date"] == "2026-08-13"
    assert response.json()["title"] == "Latest report"


def test_legacy_digest_preview_is_not_routed(client: TestClient) -> None:
    assert client.get("/api/digest/preview").status_code == 404
