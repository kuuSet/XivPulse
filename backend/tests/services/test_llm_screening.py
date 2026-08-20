from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from app import database
from app.database import Base, make_session_factory
from app.database_defaults import seed_defaults
from app.domain.paper import Paper as PaperDocument
from app.models import Analysis, Paper, PaperMatch, Profile
from app.schemas import PrefilterDecision, PrefilterResult
from app.services.analyzer import PaperAnalyzer
from app.services.paper_sync import sync_papers
from app.services.provider import GenerationResult, ProviderError
from app.settings import ProviderSettings, set_setting
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.asyncio
async def test_sync_creates_unfiltered_matches_for_every_fetched_paper() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    with factory() as session:
        seed_defaults(session)
        profile_id = session.query(Profile).one().id

    async def fetcher(*_args: object, **_kwargs: object) -> list[PaperDocument]:
        return [
            PaperDocument("2608.01001", 1, "Paper one"),
            PaperDocument("2608.01002", 1, "Paper two"),
        ]

    result = await sync_papers(
        target_date=date(2026, 8, 18),
        session_factory=factory,
        paper_fetcher=fetcher,
    )

    assert result["fetched"] == 2
    assert result["matches_created"] == 2
    with factory() as session:
        matches = session.query(PaperMatch).order_by(PaperMatch.paper_id).all()
        assert len(matches) == 2
        assert {match.profile_id for match in matches} == {profile_id}
        assert all(match.prefiltered_at is None for match in matches)


@pytest.mark.asyncio
async def test_title_prefilter_batches_papers_without_sending_abstracts() -> None:
    captured: dict[str, Any] = {}

    class FakeAdapter:
        async def generate_json_with_metadata(self, **values: Any) -> GenerationResult:
            captured.update(values)
            return GenerationResult(
                data=PrefilterResult(
                    items=[
                        PrefilterDecision(paper_id=1, decision="relevant"),
                        PrefilterDecision(paper_id=2, decision="uncertain"),
                        PrefilterDecision(paper_id=3, decision="irrelevant"),
                    ]
                ),
                transport="responses",
                provider="test",
                model="test",
            )

    analyzer = PaperAnalyzer(FakeAdapter())  # type: ignore[arg-type]
    result = await analyzer.prefilter_with_metadata(
        [
            {
                "id": 1,
                "title": "Distributed scheduling for language-model serving",
                "abstract": "SECRET_ABSTRACT_ONE",
                "categories": ["cs.DC", "cs.LG"],
            },
            {
                "id": 2,
                "title": "An ambiguous systems paper",
                "abstract": "SECRET_ABSTRACT_TWO",
                "categories": ["cs.AI"],
            },
            {
                "id": 3,
                "title": "Algebraic topology of compact manifolds",
                "abstract": "SECRET_ABSTRACT_THREE",
                "categories": ["math.AT"],
            },
        ],
        [
            {
                "name": "LLM Systems",
                "description": "Efficient inference, serving, scheduling, and memory management.",
                "enabled": True,
            }
        ],
    )

    prompt = captured["user_prompt"]
    assert "Distributed scheduling for language-model serving" in prompt
    assert "cs.DC, cs.LG" in prompt
    assert "Efficient inference, serving" in prompt
    assert "SECRET_ABSTRACT" not in prompt
    assert [item.decision for item in result.data.items] == [
        "relevant",
        "uncertain",
        "irrelevant",
    ]


@pytest.mark.asyncio
async def test_pipeline_screens_only_relevant_and_uncertain_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas import ScreeningResult
    from app.services import paper_analysis

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    with factory() as session:
        seed_defaults(session)
        set_setting(session, "api_key", "test-key")
        profile = session.query(Profile).one()
        for index in range(3):
            paper = Paper(
                arxiv_id=f"2608.2000{index}",
                title=f"Paper {index}",
                abstract=f"Abstract {index}",
                categories_json=["cs.AI"],
            )
            session.add(paper)
            session.flush()
            session.add(PaperMatch(paper_id=paper.id, profile_id=profile.id))
        direction = profile.topics[0].name
        session.commit()

    screened_titles: list[str] = []

    class FakeAnalyzer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def prefilter_with_metadata(
            self, papers: list[Paper], _topics: object
        ) -> GenerationResult:
            decisions = ("relevant", "uncertain", "irrelevant")
            return GenerationResult(
                data=PrefilterResult(
                    items=[
                        PrefilterDecision(paper_id=paper.id, decision=decisions[paper.id - 1])
                        for paper in papers
                    ]
                ),
                transport="responses",
                provider="test",
                model="test",
            )

        async def screen_with_metadata(
            self, paper: Paper, _topics: object
        ) -> GenerationResult:
            screened_titles.append(paper.title)
            return GenerationResult(
                data=ScreeningResult(
                    relevant=True,
                    relevance_score=80,
                    reason="Relevant after reading the abstract",
                    matched_topics=[direction],
                    key_contributions=[],
                    tags=[],
                ),
                transport="responses",
                provider="test",
                model="test",
            )

    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(paper_analysis, "SessionLocal", factory)
    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", FakeAnalyzer)

    result = await paper_analysis.analyze_papers(limit=0)

    assert result["prefiltered"] == 3
    assert result["prefilter_kept"] == 2
    assert result["prefilter_rejected"] == 1
    assert result["screened"] == 2
    assert screened_titles == ["Paper 0", "Paper 1"]
    with factory() as session:
        matches = session.query(PaperMatch).order_by(PaperMatch.paper_id).all()
        assert [match.prefilter_decision for match in matches] == [
            "relevant",
            "uncertain",
            "irrelevant",
        ]
        assert [match.screening_relevance_score for match in matches] == [80, 80, None]
        assert session.query(Analysis).filter_by(kind="screening").count() == 2


@pytest.mark.asyncio
async def test_provider_failure_stops_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import paper_analysis

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    Base.metadata.create_all(engine)
    with factory() as session:
        seed_defaults(session)
        profile = session.query(Profile).one()
        paper = Paper(
            arxiv_id="2608.29999",
            title="Pending paper",
            abstract="Pending abstract",
            categories_json=["cs.AI"],
        )
        session.add(paper)
        session.flush()
        session.add(PaperMatch(paper_id=paper.id, profile_id=profile.id))
        session.commit()

    class FailingAnalyzer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def prefilter_with_metadata(self, *_args: object) -> object:
            raise ProviderError("provider unavailable")

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", FailingAnalyzer)
    monkeypatch.setattr(
        paper_analysis,
        "load_runtime_settings",
        lambda _db: type(
            "Settings",
            (),
            {
                "provider": ProviderSettings(api_key="test"),
                "deep_analysis_limit": 0,
                "deep_analysis_threshold": 90,
                "prefilter_batch_size": 30,
                "schedule_timezone": "UTC",
                "analysis_budget": "economy",
                "prompt_values": lambda self: {},
            },
        )(),
    )
    monkeypatch.setattr(paper_analysis, "SessionLocal", factory)

    with pytest.raises(ProviderError, match="provider unavailable"):
        await paper_analysis.analyze_papers(limit=0)
