from __future__ import annotations

from datetime import UTC, date, datetime

import app.services.reporting.service as reporting_service
import pytest
from app.api.reports import router as reports_router
from app.models import Analysis, Paper, PaperMatch, Profile
from app.services.reporting.builder import build_daily_research_report
from app.services.reporting.service import persist_daily_report, report_snapshot
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_direction_reporting_routes_are_removed() -> None:
    paths = {route.path for route in reports_router.routes}
    assert "/api/trends/topics" not in paths
    assert "/api/trends/roadmap" not in paths


@pytest.mark.asyncio
async def test_daily_finalize_merges_report_synthesis_and_delivery_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported_progress: list[int] = []

    monkeypatch.setattr(
        reporting_service,
        "update_job_progress",
        lambda job_id, **kwargs: reported_progress.append(kwargs["progress"]),
    )
    monkeypatch.setattr(
        reporting_service,
        "create_daily_report",
        lambda **kwargs: {
            "report_snapshot_id": 7,
            "report_date": kwargs["report_date"].isoformat(),
        },
    )

    async def synthesize(*args: object, **kwargs: object) -> dict[str, str]:
        return {"report_synthesis_status": "ready"}

    async def send(*args: object, **kwargs: object) -> dict[str, int]:
        return {"email_sent": 1, "email_failed": 0}

    monkeypatch.setattr(reporting_service, "synthesize_daily_report", synthesize)
    monkeypatch.setattr(reporting_service, "send_daily_report_email", send)

    result = await reporting_service.finalize_daily_stage(
        date(2026, 8, 13), {"fetched": 12}, "job-1"
    )

    assert result == {
        "fetched": 12,
        "report_snapshot_id": 7,
        "report_date": "2026-08-13",
        "report_synthesis_status": "ready",
        "email_sent": 1,
        "email_failed": 0,
    }
    assert reported_progress == [88, 94]


def test_daily_report_uses_scores_and_saved_analysis(analytics_session: Session) -> None:
    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )

    assert report["coverage"] == {
        "paper_count": 2,
        "analyzed_paper_count": 2,
        "deep_analysis_count": 2,
        "analysis_coverage_percent": 100.0,
        "deep_analysis_coverage_percent": 100.0,
        "high_score_paper_count": 2,
        "average_score": 90.0,
    }
    assert "共纳入 2 篇" in report["executive_summary"]
    assert report["highlight_papers"][0]["arxiv_id"] == "2608.00001"
    assert report["highlight_papers"][0]["recommendation"] == "must_read"
    assert report["suggested_reading_queue"][0]["priority"] == "must_read"
    assert "topics" not in report["highlight_papers"][0]
    assert "why_it_matters" not in report["highlight_papers"][0]
    assert "reason" not in report["suggested_reading_queue"][0]
    assert "topic_insights" not in report
    assert "daily_trend" not in report

    methods = {item["name"]: item for item in report["common_methods"]}
    assert methods["模仿学习"]["count"] == 2
    assert methods["模仿学习"]["shared"] is True
    situation = report["research_situation"]
    assert situation["volume"]["today"] == 2
    assert situation["volume"]["previous_day"] == 1
    assert situation["score_summary"] == {
        "average": 90.0,
        "previous_average": 78.0,
        "change": 12.0,
        "highest": 94.0,
        "high_score_count": 2,
        "high_score_threshold": 80.0,
    }
    assert "direction_dynamics" not in situation
    assert "模仿学习" in situation["method_signals"]
    assert situation["evidence_maturity"]["status"] in {"mixed", "strong"}
    assert situation["evidence_maturity"]["shared_strong_signals"] == ["真实机器人实验"]

    overview = situation["quality_overview"]
    assert overview["qualified_paper_count"] == 2
    assert overview["score_range"] == {"lowest": 86.0, "highest": 94.0, "average": 90.0}
    assert "main_threads" not in overview
    assert overview["paper_findings"][0]["arxiv_id"] == "2608.00001"
    assert "topics" not in overview["paper_findings"][0]
    assert "Action-conditioned latent dynamics" in overview["paper_findings"][0]["contribution"]
    assert "submittedDate" in report["date_basis"]
    assert "关键词或 arXiv 分类伪造关注标签归因" in report["methodology_note"]


def test_daily_report_persists_the_score_only_contract(analytics_session: Session) -> None:
    snapshot = persist_daily_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )
    analytics_session.commit()
    payload = report_snapshot(snapshot)

    assert payload["persisted"] is True
    assert payload["snapshot_id"] == snapshot.id
    assert payload["report_date"] == "2026-08-12"
    assert payload["coverage"]["paper_count"] == 2
    assert payload["highlight_papers"][0]["score"] == 94.0
    assert payload["suggested_reading_queue"][0]["score"] == 94.0
    assert "generated_at" in payload
    assert "updated_at" in payload


def test_report_keeps_controlled_aggregates_and_paper_details(
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


def test_report_is_complete_when_no_scored_paper_exists(analytics_session: Session) -> None:
    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 20),
        timezone_name="UTC",
    )

    assert report["coverage"]["paper_count"] == 0
    assert report["coverage"]["average_score"] is None
    assert "没有已完成摘要粗排" in report["executive_summary"]
    assert report["highlight_papers"] == []
    assert report["common_methods"] == []
    assert report["common_evidence"] == []
    assert report["suggested_reading_queue"] == []
    assert report["research_situation"]["volume"]["today"] == 0
    assert report["research_situation"]["score_summary"]["average"] is None
    assert report["research_situation"]["evidence_maturity"]["status"] == "no_signal"


def test_report_falls_back_to_abstract_without_analysis(analytics_session: Session) -> None:
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
    assert report["suggested_reading_queue"][0]["priority"] == "skim"


def test_report_includes_every_scored_paper_without_direction_attribution(
    analytics_session: Session,
) -> None:
    profile = analytics_session.get(Profile, 1)
    assert profile is not None
    published_at = datetime(2026, 8, 12, 13, tzinfo=UTC)
    paper = Paper(
        arxiv_id="2608.00995",
        title="A High-scoring Systems Paper",
        abstract="A systems paper selected by the score-only screening stage.",
        authors_json=["Researcher"],
        categories_json=["cs.DC"],
        primary_category="cs.DC",
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
            prefilter_decision="relevant",
            prefiltered_at=published_at,
            screening_relevance_score=99,
            screened_at=published_at,
        )
    )
    analytics_session.commit()

    report = build_daily_research_report(
        analytics_session,
        1,
        report_date=date(2026, 8, 12),
        timezone_name="UTC",
    )

    assert report["coverage"]["paper_count"] == 3
    assert report["coverage"]["average_score"] == 93.0
    assert report["highlight_papers"][0]["paper_id"] == paper.id
    assert report["suggested_reading_queue"][0]["paper_id"] == paper.id


def test_screening_only_batch_is_not_strong_evidence(analytics_session: Session) -> None:
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
                prefilter_decision="relevant",
                prefiltered_at=published_at,
                screening_relevance_score=85,
                screened_at=published_at,
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
