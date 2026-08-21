from __future__ import annotations

from datetime import date

import pytest
from app.models import Analysis, PaperMatch
from app.services.reporting.roadmap import build_research_roadmap
from app.services.reporting.trends import build_topic_trends
from sqlalchemy import select
from sqlalchemy.orm import Session


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


def test_invalid_roadmap_window_is_rejected(analytics_session: Session) -> None:
    with pytest.raises(ValueError, match="days"):
        build_research_roadmap(analytics_session, 1, days=0)
