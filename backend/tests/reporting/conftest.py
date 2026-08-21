from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.database import Base, make_engine
from app.models import Analysis, Paper, PaperMatch, Profile
from sqlalchemy.orm import Session


@pytest.fixture()
def analytics_session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    profile = Profile()
    session.add(profile)
    session.flush()

    def add_paper(
        arxiv_id: str,
        title: str,
        abstract: str,
        published_at: datetime,
        *,
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
                prefilter_decision="relevant",
                prefiltered_at=published_at,
                screening_relevance_score=score,
                screened_at=published_at,
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
        score=78,
    )
    add_paper(
        "2608.00004",
        "Earlier Navigation Study",
        "Visual navigation in simulation.",
        datetime(2026, 8, 4, 4, tzinfo=UTC),
        score=70,
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
