from __future__ import annotations

from datetime import UTC, date, datetime

from app.models import Analysis, Paper, PaperMatch, Profile
from app.services.reporting.builder import build_daily_research_report
from app.services.reporting.roadmap import build_research_roadmap
from app.services.reporting.trends import build_topic_trends
from sqlalchemy.orm import Session


def test_llm_topic_is_counted_without_keyword_evidence(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    paper = Paper(
        arxiv_id="2608.00999",
        title="A General Robotics Systems Benchmark",
        abstract="We compare broad robotics systems without studying navigation.",
        authors_json=["Researcher"],
        categories_json=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["Navigation"],
            prefilter_decision="relevant",
            prefiltered_at=paper.published_at,
            screening_relevance_score=25,
            screened_at=paper.published_at,
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    daily = {item["name"]: item for item in trends["daily"]["topics"]}
    assert daily["Navigation"]["count"] == 1


def test_llm_topic_drives_reports_without_local_revalidation(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    paper = Paper(
        arxiv_id="2608.00996",
        title="A General Robotics Systems Benchmark",
        abstract="We compare broad robot systems without studying navigation.",
        authors_json=["Researcher"],
        categories_json=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["Navigation"],
            prefilter_decision="relevant",
            prefiltered_at=paper.published_at,
            screening_relevance_score=99,
            screened_at=paper.published_at,
        )
    )
    analytics_session.add(
        Analysis(
            paper_id=paper.id,
            profile_id=profile.id,
            kind="deep",
            status="completed",
            provider="mock",
            model="mock",
            summary="This deliberately unsupported paper must not enter the report.",
            verdict="Unsupported direction attribution.",
            weaknesses_json=["Contaminating limitation"],
            payload_json={
                "methods": ["Contaminating method"],
                "evidence": ["Contaminating evidence"],
                "recommendation": "must_read",
            },
            created_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    roadmap = build_research_roadmap(
        analytics_session,
        1,
        as_of=date(2026, 8, 12),
        timezone_name="UTC",
        days=14,
    )

    assert "Navigation" in {item["name"] for item in trends["daily"]["topics"]}
    roadmap_nodes = {item["label"]: item for item in roadmap["nodes"]}
    assert roadmap_nodes["Navigation"]["paper_count"] == 2
    assert roadmap["coverage"]["paper_count"] == 5
    assert roadmap["coverage"]["candidate_paper_count"] == 5
    assert roadmap["coverage"]["direction_attributed_paper_count"] == 5

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )
    assert report["coverage"]["candidate_paper_count"] == 3
    assert report["coverage"]["paper_count"] == 3
    assert report["coverage"]["direction_attributed_paper_count"] == 3
    assert report["coverage"]["direction_attribution_coverage_percent"] == 100.0
    assert report["research_situation"]["volume"]["today"] == 3
    assert paper.id in {item["paper_id"] for item in report["highlight_papers"]}
    assert paper.id in {item["paper_id"] for item in report["suggested_reading_queue"]}


def test_paper_match_is_the_only_llm_topic_snapshot(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    paper = Paper(
        arxiv_id="2608.00998",
        title="A Multimodal Robot Policy",
        abstract="The abstract uses different terminology from the configured phrases.",
        authors_json=["Researcher"],
        categories_json=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["VLA"],
            prefilter_decision="relevant",
            prefiltered_at=paper.published_at,
            screening_relevance_score=80,
            screened_at=paper.published_at,
        )
    )
    analytics_session.add(
        Analysis(
            paper_id=paper.id,
            profile_id=profile.id,
            kind="screening",
            status="completed",
            provider="mock",
            model="mock",
            payload_json={"matched_topics": ["  vla  ", "cs.RO"]},
            created_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    daily = {item["name"]: item for item in trends["daily"]["topics"]}
    assert daily["VLA"]["count"] == 2

