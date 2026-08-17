"""Research reporting service tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from app.database import Base, make_engine
from app.models import Analysis, Digest, Paper, PaperMatch, Profile, Topic
from app.services.directions.taxonomy import stamp_direction_attribution
from app.services.reporting.builder import build_daily_research_report
from app.services.reporting.roadmap import build_research_roadmap
from app.services.reporting.trends import build_topic_trends
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture()
def analytics_session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    profile = Profile()
    session.add(profile)
    session.flush()
    configured_topics = {
        "VLA": ["vision language action", "vla"],
        "World Models": ["world model"],
        "Robot Learning": ["robot learning", "behavior cloning"],
        "Navigation": ["visual navigation"],
    }
    for name, keywords in configured_topics.items():
        session.add(
            Topic(
                profile_id=profile.id,
                name=name,
                keywords_json=keywords,
                arxiv_categories_json=["cs.RO"],
                enabled=True,
            )
        )

    def add_paper(
        arxiv_id: str,
        title: str,
        abstract: str,
        published_at: datetime,
        *,
        topics: list[str],
        keywords: list[str],
        score: float,
        analysis: dict[str, object] | None = None,
    ) -> None:
        paper = Paper(
            arxiv_id=arxiv_id,
            title=title,
            abstract=abstract,
            authors_json=["Researcher"],
            categories_json=["cs.RO"],
            primary_category="cs.RO",
            published_at=published_at,
            first_seen_at=published_at,
            last_seen_at=published_at,
        )
        session.add(paper)
        session.flush()
        session.add(
            PaperMatch(
                paper_id=paper.id,
                profile_id=profile.id,
                matched_topics_json=topics,
                matched_keywords_json=keywords,
                semantic_score=score / 100,
                screening_relevance_score=score,
                reason=f"Relevant to {', '.join(topics)}",
            )
        )
        if analysis is not None:
            session.add(
                Analysis(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    kind="deep",
                    status="completed",
                    provider="mock",
                    model="mock",
                    summary=str(analysis["summary"]),
                    verdict=str(analysis["verdict"]),
                    relevance_score=score,
                    novelty_score=float(analysis.get("novelty", 80)),
                    rigor_score=float(analysis.get("rigor", 80)),
                    impact_score=float(analysis.get("impact", 80)),
                    tags_json=list(analysis.get("tags", [])),
                    weaknesses_json=list(analysis.get("weaknesses", [])),
                    payload_json={
                        "methods": analysis.get("methods", []),
                        "evidence": analysis.get("evidence", []),
                        "limitations": analysis.get("limitations", []),
                        "follow_up_questions": analysis.get("follow_up_questions", []),
                        "key_contributions": analysis.get("key_contributions", []),
                        "recommendation": analysis.get("recommendation", "read"),
                    },
                    created_at=published_at,
                )
            )

    add_paper(
        "2608.00001",
        "Vision Language Action with a World Model",
        "Real robot experiments and an ablation study demonstrate generalization.",
        datetime(2026, 8, 12, 2, tzinfo=UTC),
        topics=["VLA", "World Models"],
        keywords=["vision language action", "world model"],
        score=94,
        analysis={
            "summary": "A world model improves a vision-language-action policy.",
            "verdict": "High relevance with direct robot evidence.",
            "tags": ["Embodied AI"],
            "methods": ["Latent world model", "Behavior cloning"],
            "evidence": ["12 real-robot tasks", "Ablation over planning horizon"],
            "weaknesses": ["Evaluation uses one robot platform"],
            "limitations": ["Long-horizon failures remain"],
            "key_contributions": ["Action-conditioned latent dynamics"],
            "recommendation": "must_read",
            "novelty": 92,
            "rigor": 88,
            "impact": 90,
        },
    )
    add_paper(
        "2608.00002",
        "Scalable Robot Learning via Behavior Cloning",
        "A benchmark and real robot evaluation across multiple tasks.",
        datetime(2026, 8, 12, 7, tzinfo=UTC),
        topics=["Robot Learning"],
        keywords=["robot learning", "behavior cloning"],
        score=86,
        analysis={
            "summary": "Large-scale behavior cloning for robot control.",
            "verdict": "Useful baseline and scaling evidence.",
            "tags": ["Embodied AI"],
            "methods": ["Behavior cloning"],
            "evidence": ["20 real-robot tasks"],
            "weaknesses": ["Evaluation uses one robot platform"],
            "recommendation": "read",
        },
    )
    add_paper(
        "2608.00003",
        "Previous VLA Study",
        "A simulation benchmark.",
        datetime(2026, 8, 11, 4, tzinfo=UTC),
        topics=["VLA"],
        keywords=["vision language action"],
        score=78,
    )
    add_paper(
        "2608.00004",
        "Earlier Navigation Study",
        "Visual navigation in simulation.",
        datetime(2026, 8, 4, 4, tzinfo=UTC),
        topics=["Navigation"],
        keywords=["visual navigation"],
        score=70,
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_daily_and_weekly_trends_are_deterministic(analytics_session: Session) -> None:
    first = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    second = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    assert first == second
    assert first["daily"]["paper_count"] == 2
    assert first["daily"]["candidate_paper_count"] == 2
    assert first["daily"]["direction_attributed_paper_count"] == 2
    assert first["daily"]["relevant_paper_count"] == 2
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
    analytics_session.add(
        Digest(
            profile_id=1,
            digest_date=date(2026, 8, 12),
            title="complete",
            status="ready",
        )
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
    assert explicit["as_of"] == "2026-08-13"
    assert explicit["awaiting_daily_update"] is False
    assert explicit["daily"]["paper_count"] == 0


def test_research_roadmap_has_topic_nodes_relations_and_timeline(
    analytics_session: Session,
) -> None:
    roadmap = build_research_roadmap(
        analytics_session,
        1,
        as_of=date(2026, 8, 12),
        timezone_name="UTC",
        days=14,
    )
    nodes = {item["label"]: item for item in roadmap["nodes"]}
    assert roadmap["coverage"]["paper_count"] == 4
    assert set(nodes) == {"VLA", "World Models", "Robot Learning", "Navigation"}
    assert all(item["sources"] == ["followed_topic"] for item in nodes.values())
    assert "cs.RO" not in nodes
    assert "Embodied AI" not in nodes
    assert nodes["VLA"]["paper_count"] == 2
    assert nodes["VLA"]["status"] == "emerging"
    assert "模仿学习" in nodes["VLA"]["methods"]
    assert set(nodes["VLA"]["keywords"]) == {"vision language action", "vla"}
    assert "robot learning" not in nodes["VLA"]["keywords"]

    node_id = {item["label"]: item["id"] for item in roadmap["nodes"]}
    relation = next(
        item
        for item in roadmap["relations"]
        if {item["source"], item["target"]} == {node_id["VLA"], node_id["World Models"]}
    )
    assert relation["weight"] == 1
    assert relation["directed"] is False
    assert len(roadmap["timeline"]) == 2
    assert "不代表引用" in roadmap["relation_basis"]


def test_trends_and_roadmap_reject_non_followed_labels(
    analytics_session: Session,
) -> None:
    first_match = analytics_session.scalar(select(PaperMatch).where(PaperMatch.paper_id == 1))
    assert first_match is not None
    first_match.matched_topics_json = [
        "vla",
        "World Models",
        "Hallucinated Research Direction",
        "cs.RO",
    ]

    category_only_match = analytics_session.scalar(
        select(PaperMatch).where(PaperMatch.paper_id == 4)
    )
    assert category_only_match is not None
    category_only_match.matched_topics_json = []
    category_only_match.matched_keywords_json = ["Navigation"]
    category_only_match.paper.title = "Earlier Robot Systems Study"
    category_only_match.paper.abstract = "A simulation benchmark without a specific direction."
    first_analysis = analytics_session.scalar(select(Analysis).where(Analysis.paper_id == 1))
    assert first_analysis is not None
    first_analysis.tags_json = [*first_analysis.tags_json, "Navigation"]
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    roadmap = build_research_roadmap(
        analytics_session,
        1,
        as_of=date(2026, 8, 12),
        timezone_name="UTC",
        days=14,
    )

    allowed = {"VLA", "World Models", "Robot Learning", "Navigation"}
    trend_labels = {
        topic["name"]
        for period in (trends["daily"], trends["weekly"])
        for topic in period["topics"]
    }
    roadmap_labels = {node["label"] for node in roadmap["nodes"]}
    assert trend_labels <= allowed
    assert roadmap_labels <= allowed
    assert "VLA" in trend_labels  # Profile spelling wins over the lower-case snapshot.
    assert "Navigation" not in roadmap_labels
    assert {"cs.RO", "Embodied AI", "Hallucinated Research Direction"}.isdisjoint(
        trend_labels | roadmap_labels
    )


def test_historical_formal_name_without_direction_evidence_is_not_counted(
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
            matched_keywords_json=[],
            semantic_score=0.25,
            screening_relevance_score=25,
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    daily = {item["name"]: item for item in trends["daily"]["topics"]}
    assert "Navigation" not in daily


def test_valid_but_unsupported_topic_id_is_not_counted(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    navigation = next(topic for topic in profile.topics if topic.name == "Navigation")
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
            topic_id=navigation.id,
            matched_topics_json=["Navigation"],
            matched_keywords_json=[],
            semantic_score=0.99,
            screening_relevance_score=99,
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

    assert "Navigation" not in {item["name"] for item in trends["daily"]["topics"]}
    roadmap_nodes = {item["label"]: item for item in roadmap["nodes"]}
    assert roadmap_nodes["Navigation"]["paper_count"] == 1
    assert roadmap["coverage"]["paper_count"] == 4
    assert roadmap["coverage"]["candidate_paper_count"] == 5
    assert roadmap["coverage"]["direction_attributed_paper_count"] == 4

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )
    assert report["coverage"]["candidate_paper_count"] == 3
    assert report["coverage"]["paper_count"] == 2
    assert report["coverage"]["direction_attributed_paper_count"] == 2
    assert report["coverage"]["direction_attribution_coverage_percent"] == 66.7
    assert report["research_situation"]["volume"]["today"] == 2
    assert paper.id not in {item["paper_id"] for item in report["highlight_papers"]}
    assert paper.id not in {item["paper_id"] for item in report["suggested_reading_queue"]}
    assert "Contaminating method" not in {item["name"] for item in report["common_methods"]}
    assert "Contaminating evidence" not in {item["name"] for item in report["common_evidence"]}
    assert "Contaminating limitation" not in {
        item["text"] for item in report["limitations_and_research_gaps"]
    }


def test_direction_context_anchor_blocks_cross_domain_trend_attribution(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    robot_learning = next(topic for topic in profile.topics if topic.name == "Robot Learning")
    robot_learning.context_keywords = ["robot", "humanoid"]

    paper = Paper(
        arxiv_id="2608.00994",
        title="Behavior Cloning for Financial Decision Making",
        abstract="We learn a policy from accounting records.",
        authors_json=["Researcher"],
        categories_json=["cs.LG"],
        primary_category="cs.LG",
        published_at=datetime(2026, 8, 12, 14, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 14, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 14, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["Robot Learning"],
            matched_keywords_json=["behavior cloning"],
            semantic_score=0.8,
            screening_relevance_score=80,
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    daily = {item["name"]: item for item in trends["daily"]["topics"]}

    assert daily["Robot Learning"]["count"] == 1
    assert trends["daily"]["candidate_paper_count"] == 3
    assert trends["daily"]["paper_count"] == 2


def test_exclusion_term_vetoes_keyword_only_direction_attribution(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    vla = next(topic for topic in profile.topics if topic.name == "VLA")
    vla.negative_keywords = ["vehicle-to-load"]
    paper = Paper(
        arxiv_id="2608.00997",
        title="VLA Vehicle-to-Load Power Systems",
        abstract="A power-grid study rather than a robot policy.",
        authors_json=["Researcher"],
        categories_json=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["VLA"],
            matched_keywords_json=["VLA"],
            semantic_score=0.2,
            screening_relevance_score=20,
        )
    )
    analytics_session.commit()

    trends = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    daily = {item["name"]: item for item in trends["daily"]["topics"]}
    assert daily["VLA"]["count"] == 1


def test_only_current_taxonomy_screening_can_establish_a_formal_direction(
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
            matched_keywords_json=[],
            semantic_score=0.8,
            screening_relevance_score=80,
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
    assert daily["VLA"]["count"] == 1

    screening = analytics_session.scalar(select(Analysis).where(Analysis.paper_id == paper.id))
    assert screening is not None
    screening.payload_json = stamp_direction_attribution(
        {"relevant": True, "matched_topics": ["  vla  ", "cs.RO"]},
        profile.topics,
    )
    analytics_session.commit()

    current = build_topic_trends(analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC")
    current_daily = {item["name"]: item for item in current["daily"]["topics"]}
    # Current AI screening may confirm a controlled direction even when the
    # paper uses new terminology that does not repeat a configured phrase.
    assert current_daily["VLA"]["count"] == 2

    paper.title = "Vision Language Action for a Multimodal Robot Policy"
    analytics_session.commit()
    evidenced = build_topic_trends(
        analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC"
    )
    evidenced_daily = {item["name"]: item for item in evidenced["daily"]["topics"]}
    assert evidenced_daily["VLA"]["count"] == 2

    vla = next(topic for topic in profile.topics if topic.name == "VLA")
    vla.negative_keywords = ["never-present-exclusion"]
    analytics_session.commit()
    changed_taxonomy = build_topic_trends(
        analytics_session, 1, as_of=date(2026, 8, 12), timezone_name="UTC"
    )
    changed_daily = {item["name"]: item for item in changed_taxonomy["daily"]["topics"]}
    # The stamped AI attribution is stale, but current local direction evidence
    # remains independently sufficient for the paper.
    assert changed_daily["VLA"]["count"] == 2


def test_daily_report_contains_all_research_sections(analytics_session: Session) -> None:
    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )
    assert report["coverage"] == {
        "paper_count": 2,
        "candidate_paper_count": 2,
        "direction_attributed_paper_count": 2,
        "relevant_paper_count": 2,
        "direction_attribution_coverage_percent": 100.0,
        "analyzed_paper_count": 2,
        "deep_analysis_count": 2,
        "analysis_coverage_percent": 100.0,
        "deep_analysis_coverage_percent": 100.0,
        "high_score_paper_count": 2,
    }
    assert "共纳入 2 篇" in report["executive_summary"]
    assert report["highlight_papers"][0]["arxiv_id"] == "2608.00001"
    assert report["highlight_papers"][0]["recommendation"] == "must_read"
    assert report["suggested_reading_queue"][0]["priority"] == "must_read"

    methods = {item["name"]: item for item in report["common_methods"]}
    assert methods["模仿学习"]["count"] == 2
    assert methods["模仿学习"]["shared"] is True
    assert report["limitations_and_research_gaps"] == []
    overview_limitations = {
        item["limitation"]
        for item in report["research_situation"]["quality_overview"]["paper_findings"]
    }
    assert "Evaluation uses one robot platform" in overview_limitations
    assert report["topic_insights"][0]["paper_count"] >= 1
    assert report["daily_trend"]["previous_paper_count"] == 1
    situation = report["research_situation"]
    assert situation["volume"]["today"] == 2
    assert situation["volume"]["previous_day"] == 1
    assert situation["direction_dynamics"]["active"][:3] == [
        "Robot Learning",
        "World Models",
        "VLA",
    ]
    assert "模仿学习" in situation["method_signals"]
    assert "当日共纳入 2 篇" in situation["synthesis"]
    assert situation["evidence_maturity"]["status"] in {"mixed", "strong"}
    assert situation["evidence_maturity"]["strong_signal_count"] == 1
    assert situation["evidence_maturity"]["shared_strong_signals"] == ["真实机器人实验"]
    assert situation["evidence_maturity"]["deep_analysis_coverage_percent"] == 100.0
    overview = situation["quality_overview"]
    assert overview["score_threshold"] == 80.0
    assert overview["qualified_paper_count"] == 2
    assert overview["included_paper_count"] == 2
    assert overview["deep_analysis_count"] == 2
    assert overview["paper_findings"][0]["arxiv_id"] == "2608.00001"
    assert overview["paper_findings"][0]["analysis_kind"] == "deep"
    assert "Action-conditioned latent dynamics" in overview["paper_findings"][0]["contribution"]
    assert "模仿学习" in overview["comparison"]
    assert "submittedDate" in report["date_basis"]
    assert "按设置时区" in report["date_basis"]
    assert "submittedDate" in report["methodology_note"]


def test_report_aggregates_controlled_labels_but_keeps_paper_level_details(
    analytics_session: Session,
) -> None:
    paper = analytics_session.scalar(select(Paper).where(Paper.arxiv_id == "2608.00001"))
    assert paper is not None
    analysis = next(item for item in paper.analyses if item.kind == "deep")
    long_method = (
        "The paper trains a vision language action transformer in three stages, "
        "then adapts it with behavior cloning and a latent world model on every task."
    )
    long_evidence = (
        "Across twelve real robot tasks and four unseen environments, the method "
        "reports detailed success rates, confidence intervals, and an ablation study."
    )
    analysis.payload_json = {
        **analysis.payload_json,
        "methods": [long_method],
        "evidence": [long_evidence],
    }
    analytics_session.commit()

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )

    assert long_method not in {item["name"] for item in report["common_methods"]}
    assert long_evidence not in {item["name"] for item in report["common_evidence"]}
    highlight = next(item for item in report["highlight_papers"] if item["paper_id"] == paper.id)
    assert long_method in highlight["methods"]
    assert long_evidence in highlight["evidence"]


def test_report_is_complete_when_no_paper_or_analysis_exists(
    analytics_session: Session,
) -> None:
    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 20),
        timezone_name="UTC",
    )
    assert report["coverage"]["paper_count"] == 0
    assert "未发现" in report["executive_summary"]
    assert report["topic_insights"] == []
    assert report["highlight_papers"] == []
    assert report["common_methods"] == []
    assert report["common_evidence"] == []
    assert report["limitations_and_research_gaps"] == []
    assert report["suggested_reading_queue"] == []
    assert report["research_situation"]["volume"]["today"] == 0
    assert report["research_situation"]["evidence_maturity"]["status"] == "no_signal"
    assert "低活动日" in report["research_situation"]["synthesis"]


def test_report_falls_back_to_abstract_without_any_analysis(
    analytics_session: Session,
) -> None:
    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 11),
        timezone_name="UTC",
    )
    assert report["coverage"]["paper_count"] == 1
    assert report["coverage"]["analyzed_paper_count"] == 0
    assert report["highlight_papers"][0]["analysis_available"] is False
    assert report["highlight_papers"][0]["summary"] == "A simulation benchmark."
    assert report["common_methods"] == []
    assert "视觉-语言-动作模型" in report["highlight_papers"][0]["methods"]
    assert any(item["name"] == "基准评测" for item in report["common_evidence"])
    assert report["limitations_and_research_gaps"] == []
    assert report["suggested_reading_queue"][0]["priority"] == "skim"


def test_daily_report_separates_candidates_from_formal_direction_papers(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    paper = Paper(
        arxiv_id="2608.00995",
        title="Transformer Benchmark for an Unrelated Candidate",
        abstract=(
            "A benchmark with a user study and real robot experiments, but no "
            "configured research-direction evidence."
        ),
        authors_json=["Researcher"],
        categories_json=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
    )
    analytics_session.add(paper)
    analytics_session.flush()
    analytics_session.add(
        PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=["cs.RO", "Navigation"],
            semantic_score=0.99,
            screening_relevance_score=99,
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
            summary="An intentionally unrelated analyzed candidate.",
            relevance_score=99,
            payload_json={
                "methods": ["Candidate-only method"],
                "evidence": ["Candidate-only evidence"],
                "limitations": ["Candidate-only limitation"],
            },
            created_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        )
    )
    analytics_session.commit()

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )

    assert report["coverage"]["candidate_paper_count"] == 3
    assert report["coverage"]["paper_count"] == 2
    assert report["coverage"]["direction_attribution_coverage_percent"] == 66.7
    assert report["daily_trend"]["candidate_paper_count"] == 3
    assert report["daily_trend"]["paper_count"] == 2
    assert paper.id not in {item["paper_id"] for item in report["highlight_papers"]}
    assert paper.id not in {item["paper_id"] for item in report["suggested_reading_queue"]}
    assert "Candidate-only method" not in {item["name"] for item in report["common_methods"]}
    assert "Candidate-only evidence" not in {item["name"] for item in report["common_evidence"]}
    assert "Candidate-only limitation" not in {
        item["text"] for item in report["limitations_and_research_gaps"]
    }
    overview_ids = {
        item["paper_id"]
        for item in report["research_situation"]["quality_overview"]["paper_findings"]
    }
    assert paper.id not in overview_ids


def test_screening_only_batch_cannot_be_reported_as_strong_evidence(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    for index in range(3):
        published_at = datetime(2026, 8, 13, index + 1, tzinfo=UTC)
        paper = Paper(
            arxiv_id=f"2608.0100{index}",
            title=f"Vision Language Action Study {index}",
            abstract=(
                "Vision language action with real robot experiments, ablation, "
                "generalization, theoretical analysis, and a user study."
            ),
            authors_json=["Researcher"],
            categories_json=["cs.RO"],
            primary_category="cs.RO",
            published_at=published_at,
            first_seen_at=published_at,
            last_seen_at=published_at,
        )
        analytics_session.add(paper)
        analytics_session.flush()
        analytics_session.add(
            PaperMatch(
                paper_id=paper.id,
                profile_id=profile.id,
                matched_topics_json=["VLA"],
                matched_keywords_json=["vision language action"],
                semantic_score=0.85,
                screening_relevance_score=85,
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
                summary="Screening only.",
                payload_json=stamp_direction_attribution(
                    {"matched_topics": ["VLA"]}, profile.topics
                ),
                created_at=published_at,
            )
        )
    analytics_session.commit()

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 13),
        timezone_name="UTC",
    )

    maturity = report["research_situation"]["evidence_maturity"]
    assert report["coverage"]["analysis_coverage_percent"] == 100.0
    assert report["coverage"]["deep_analysis_count"] == 0
    assert report["coverage"]["deep_analysis_coverage_percent"] == 0.0
    assert maturity["status"] == "insufficient_analysis"
    assert maturity["deep_analysis_coverage_percent"] == 0.0


def test_invalid_roadmap_window_is_rejected(analytics_session: Session) -> None:
    with pytest.raises(ValueError, match="days"):
        build_research_roadmap(analytics_session, 1, days=0)
