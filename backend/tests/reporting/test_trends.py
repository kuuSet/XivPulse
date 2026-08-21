from __future__ import annotations

from datetime import date

import pytest
from app.models import DailyResearchReport, Job
from app.services.reporting.trends import build_topic_trends
from sqlalchemy.orm import Session


def test_daily_and_weekly_trends_are_deterministic(analytics_session: Session) -> None:
    first = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    second = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    assert first == second
    assert first["daily"]["paper_count"] == 2
    assert first["daily"]["candidate_paper_count"] == 2
    assert first["daily"]["direction_attributed_paper_count"] == 2
    assert first["weekly"]["paper_count"] == 3

    daily = {item["name"]: item for item in first["daily"]["topics"]}
    assert set(daily) == {"VLA", "World Models", "Robot Learning"}
    assert daily["VLA"]["count"] == 1
    assert daily["VLA"]["previous_count"] == 1
    assert daily["VLA"]["direction"] == "stable"
    assert daily["World Models"]["direction"] == "new"
    assert daily["VLA"]["average_score"] == 94.0
    assert all(item["sources"] == ["followed_topic"] for item in daily.values())

    weekly = {item["name"]: item for item in first["weekly"]["topics"]}
    assert weekly["Navigation"]["count"] == 0
    assert weekly["Navigation"]["direction"] == "falling"


def test_default_trends_keep_latest_complete_day_until_daily_update(
    analytics_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    analytics_session.add_all(
        [
            DailyResearchReport(
                profile_id=1,
                report_date=date(2026, 8, 13),
                payload_json={},
            ),
            Job(
                kind="daily",
                status="completed",
                result_json={"fetch_date": "2026-08-12"},
            ),
        ]
    )
    analytics_session.commit()
    monkeypatch.setattr(
        "app.services.reporting.trends._target_day",
        lambda value, timezone: date(2026, 8, 13) if value is None else value,
    )

    pending = build_topic_trends(analytics_session, 1, timezone_name="UTC")
    explicit = build_topic_trends(
        analytics_session, 1, as_of=date(2026, 8, 13), timezone_name="UTC"
    )

    assert pending["as_of"] == "2026-08-12"
    assert pending["requested_as_of"] == "2026-08-13"
    assert pending["awaiting_daily_update"] is True
    assert pending["daily"]["paper_count"] == 2
    assert analytics_session.query(DailyResearchReport).one().report_date == date(2026, 8, 13)
    assert explicit["as_of"] == "2026-08-13"
    assert explicit["awaiting_daily_update"] is False
    assert explicit["daily"]["paper_count"] == 0
