from __future__ import annotations

import pytest
from app.bootstrap import bootstrap_application
from app.database import make_session_factory
from app.integrations.llm.contracts import GenerationResult, ProviderError
from app.models import Analysis, Paper, PaperMatch, Profile
from app.schemas import PrefilterDecision, PrefilterResult
from app.settings import ProviderSettings, StageProviders
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.asyncio
async def test_pipeline_screens_only_relevant_and_uncertain_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.analysis import pipeline as paper_analysis
    from app.schemas import ScreeningResult

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    bootstrap_application(factory)
    with factory() as session:
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
        session.commit()

    screened_titles: list[str] = []
    progress_updates: list[dict[str, object]] = []

    def record_progress(_job_id: str | None, **values: object) -> None:
        progress_updates.append(values)

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

        async def screen_with_metadata(self, paper: Paper, _topics: object) -> GenerationResult:
            screened_titles.append(paper.title)
            return GenerationResult(
                data=ScreeningResult(score=80),
                transport="responses",
                provider="test",
                model="test",
            )

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", FakeAnalyzer)

    result = await paper_analysis.analyze_papers(
        limit=0,
        job_id="job-1",
        session_factory=factory,
        progress_callback=record_progress,
    )

    assert result["prefiltered"] == 3
    assert result["prefilter_kept"] == 2
    assert result["prefilter_rejected"] == 1
    assert result["screened"] == 2
    assert screened_titles == ["Paper 0", "Paper 1"]
    assert [update["progress"] for update in progress_updates] == [35, 70, 70]
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
    from app.modules.analysis import pipeline as paper_analysis

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = make_session_factory(engine)
    bootstrap_application(factory)
    with factory() as session:
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
                "providers": StageProviders(
                    prefilter=ProviderSettings(api_key="test"),
                    screening=ProviderSettings(api_key="test"),
                    deep=ProviderSettings(api_key="test"),
                    synthesis=ProviderSettings(api_key="test"),
                ),
                "deep_analysis_limit": 0,
                "deep_analysis_threshold": 90,
                "prefilter_batch_size": 30,
                "schedule_timezone": "UTC",
                "analysis_budget": "economy",
                "prompt_template_id": "balanced-review",
                "deep_analysis_prompt": None,
            },
        )(),
    )
    with pytest.raises(ProviderError, match="provider unavailable"):
        await paper_analysis.analyze_papers(limit=0, session_factory=factory)
