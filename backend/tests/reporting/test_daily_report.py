from __future__ import annotations

from datetime import UTC, date, datetime

import app.services.reporting.service as reporting_service
import pytest
from app.models import Analysis, Paper, PaperMatch, Profile
from app.services.reporting.builder import build_daily_research_report
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.mark.asyncio
async def test_daily_finalize_returns_the_saved_report_without_a_legacy_digest(
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


def test_daily_report_accepts_llm_topics_and_rejects_unknown_labels(
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
    assert report["coverage"]["paper_count"] == 3
    assert report["coverage"]["direction_attribution_coverage_percent"] == 100.0
    assert report["daily_trend"]["candidate_paper_count"] == 3
    assert report["daily_trend"]["paper_count"] == 3
    assert paper.id in {item["paper_id"] for item in report["highlight_papers"]}
    assert paper.id in {item["paper_id"] for item in report["suggested_reading_queue"]}
    assert {item["name"] for item in report["daily_trend"]["topics"]} >= {"Navigation"}
    overview_ids = {
        item["paper_id"]
        for item in report["research_situation"]["quality_overview"]["paper_findings"]
    }
    assert paper.id in overview_ids


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
                summary="Screening only.",
                payload_json={"matched_topics": ["VLA"]},
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
