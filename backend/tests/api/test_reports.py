"""Daily-report HTTP contract tests."""

from __future__ import annotations

from datetime import date
from typing import Any

import app.modules.reports.routes as reports_api
import pytest
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
    assert response.status_code == 200
    assert response.json()["report_date"] == "2026-08-13"
    assert response.json()["title"] == "Latest report"


def test_synthesis_route_uses_the_application_database(
    client: TestClient,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_date = date(2026, 8, 14)
    _insert_report(session_factory, report_date, "Application report")
    captured: dict[str, Any] = {}

    async def synthesize(
        selected_date: date,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.update(kwargs)
        assert selected_date == report_date
        return {"report_synthesis_status": "ready"}

    monkeypatch.setattr(reports_api, "synthesize_daily_report", synthesize)

    response = client.post(f"/api/reports/daily/synthesize?date={report_date.isoformat()}")

    assert response.status_code == 200
    assert response.json()["title"] == "Application report"
    assert captured["session_factory"] is session_factory
