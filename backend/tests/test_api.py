from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app import database
from app.database import Base
from app.database_defaults import seed_defaults
from app.domain.paper import Paper as PaperDocument
from app.main import app
from app.models import (
    Analysis,
    AppSetting,
    DailyResearchReport,
    Digest,
    EmailDelivery,
    Feedback,
    Paper,
    PaperEmbedding,
    PaperMatch,
    Profile,
    ReportSynthesisRun,
    ResearchProject,
    ScreeningQueueItem,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    with database.SessionLocal() as session:
        seed_defaults(session)

    import app.pipeline as pipeline_module
    import app.runtime as runtime
    from app.services import paper_sync
    from app.workflow import daily_pipeline

    monkeypatch.setattr(
        paper_sync,
        "semantic_match_papers",
        lambda _db, _papers, _topics: {},
    )
    monkeypatch.setattr(runtime, "_initialized", True)
    monkeypatch.setattr(daily_pipeline, "_active_job_id", None)
    monkeypatch.setattr(
        pipeline_module,
        "PIPELINE_LOCK_PATH",
        Path.cwd() / ".pytest_pipeline.lock",
    )
    pipeline_module.PIPELINE_LOCK_PATH.unlink(missing_ok=True)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        pipeline_module.PIPELINE_LOCK_PATH.unlink(missing_ok=True)
        engine.dispose()


def _insert_paper(
    *,
    arxiv_id: str = "2608.01234",
    title: str = "An Embodied AI Paper",
    screened: bool = False,
) -> int:
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).first()
        assert profile is not None
        paper = Paper(
            arxiv_id=arxiv_id,
            latest_version=1,
            title=title,
            abstract="A robot learning and world model study.",
            authors_json=["A. Author"],
            categories_json=["cs.RO"],
            primary_category="cs.RO",
            abs_url="https://arxiv.org/abs/2608.01234",
            pdf_url="https://arxiv.org/pdf/2608.01234",
            published_at=datetime.now(UTC),
        )
        session.add(paper)
        session.flush()
        session.add(
            PaperMatch(
                paper_id=paper.id,
                profile_id=profile.id,
                matched_topics_json=["具身智能"],
                matched_keywords_json=["robot learning"],
                relevance_score=92,
                reason="与具身智能画像高度相关",
                screened_at=datetime.now(UTC) if screened else None,
            )
        )
        session.commit()
        return paper.id


def test_semantic_recall_uses_cached_fake_vectors_without_keyword_gate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import semantic

    embedding_calls: list[list[str]] = []

    class FakeEmbedding:
        def embed(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
            assert batch_size == semantic.EMBEDDING_BATCH_SIZE
            embedding_calls.append(texts)
            if texts and texts[0].startswith(semantic.QUERY_INSTRUCTION):
                return [
                    [1.0, 0.0, *([0.0] * 382)] if index == 0 else [0.0, 1.0, *([0.0] * 382)]
                    for index, _ in enumerate(texts)
                ]
            return [[0.78, 0.0, 0.6258, *([0.0] * 381)] for _ in texts]

    monkeypatch.setattr(semantic, "_model", FakeEmbedding())
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        paper = Paper(
            arxiv_id="2608.08888",
            title="A New Cross-Modal Policy Architecture",
            abstract="This abstract deliberately uses new terminology.",
            authors_json=["Researcher"],
            categories_json=["cs.RO"],
            primary_category="cs.RO",
        )
        session.add(paper)
        session.flush()
        results = semantic.match_papers(session, [paper], profile.topics)
        cached_results = semantic.match_papers(session, [paper], profile.topics)
        session.commit()
        assert results[paper.id].score == pytest.approx(0.78)
        assert cached_results[paper.id].score == pytest.approx(0.78)
        assert session.query(PaperEmbedding).filter_by(paper_id=paper.id).count() == 1
        assert len(embedding_calls) == 3
        assert all(text.isascii() for text in embedding_calls[0])
        assert not embedding_calls[1][0].startswith("passage:")
        assert semantic.is_candidate(
            semantic_score=semantic.RECALL_THRESHOLDS["high"],
            mode="high",
        )
        assert not semantic.is_candidate(
            semantic_score=semantic.RECALL_THRESHOLDS["high"] - 0.01,
            mode="high",
        )
        assert not semantic.is_candidate(
            semantic_score=0.1,
            mode="precise",
        )
        assert semantic.is_candidate(
            semantic_score=0.1,
            mode="precise",
            watched_author=True,
        )


@pytest.mark.asyncio
async def test_screening_queue_processes_65_candidates_in_three_batches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas import ScreeningResult
    from app.services import paper_analysis
    from app.services import provider as provider_module

    assert client.put("/api/settings", json={"screen_batch_size": 30}).status_code == 200
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        direction = profile.topics[0].name
        for index in range(65):
            paper = Paper(
                arxiv_id=f"2608.1{index:04d}",
                title=f"Semantic candidate {index}",
                abstract="A candidate for bounded fake screening.",
                authors_json=["Researcher"],
                categories_json=["cs.AI"],
                primary_category="cs.AI",
                last_seen_at=datetime.now(UTC),
            )
            session.add(paper)
            session.flush()
            session.add(
                PaperMatch(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    semantic_score=0.81,
                )
            )
        session.commit()

    class FakePaperAnalyzer:
        def __init__(self, _adapter: object, **_options: object) -> None:
            pass

        async def screen_with_metadata(self, _paper: Paper, _profile: Profile) -> object:
            return provider_module.GenerationResult(
                data=ScreeningResult(
                    relevant=True,
                    relevance_score=82,
                    reason="Relevant fake result",
                    matched_topics=[direction],
                    key_contributions=[],
                    tags=[],
                ),
                transport="mock",
                provider="test",
                model="test",
            )

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", FakePaperAnalyzer)
    monkeypatch.setattr(paper_analysis, "SCREENING_RETRY_DELAYS", (0.0, 0.0))
    result = await paper_analysis.analyze_papers(limit=0)
    assert result["screened"] == 65
    assert result["screening_batches"] == 3
    assert result["screening_total_batches"] == 3
    assert result["screening_failed"] == 0
    with database.SessionLocal() as session:
        assert session.query(ScreeningQueueItem).filter_by(status="completed").count() == 65
        assert session.query(Analysis).filter_by(kind="screening").count() == 65


@pytest.mark.asyncio
async def test_analysis_does_not_hide_constructor_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import paper_analysis

    class BrokenAnalyzer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise TypeError("broken analyzer constructor")

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", BrokenAnalyzer)

    with pytest.raises(TypeError, match="broken analyzer constructor"):
        await paper_analysis.analyze_papers(limit=0)


@pytest.mark.asyncio
async def test_screening_retries_and_keeps_final_failure_in_queue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas import ScreeningResult
    from app.services import paper_analysis
    from app.services import provider as provider_module

    attempts: dict[str, int] = {}
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        direction = profile.topics[0].name
        for suffix in ("90", "91"):
            paper = Paper(
                arxiv_id=f"2608.00{suffix}",
                title=f"Retry candidate {suffix}",
                abstract="A bounded retry test.",
                authors_json=["Researcher"],
                categories_json=["cs.AI"],
                primary_category="cs.AI",
                last_seen_at=datetime.now(UTC),
            )
            session.add(paper)
            session.flush()
            session.add(
                PaperMatch(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    semantic_score=0.81,
                )
            )
        session.commit()

    class RetryAnalyzer:
        def __init__(self, _adapter: object, **_options: object) -> None:
            pass

        async def screen_with_metadata(self, paper: Paper, _profile: Profile) -> object:
            attempts[paper.arxiv_id] = attempts.get(paper.arxiv_id, 0) + 1
            if paper.arxiv_id.endswith("91") or attempts[paper.arxiv_id] < 3:
                raise provider_module.ProviderError("simulated timeout")
            return provider_module.GenerationResult(
                data=ScreeningResult(
                    relevant=True,
                    relevance_score=82,
                    reason="Recovered on third attempt",
                    matched_topics=[direction],
                    key_contributions=[],
                    tags=[],
                ),
                transport="mock",
                provider="test",
                model="test",
            )

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", RetryAnalyzer)
    monkeypatch.setattr(paper_analysis, "SCREENING_RETRY_DELAYS", (0.0, 0.0))
    result = await paper_analysis.analyze_papers(limit=0)
    assert result["screened"] == 1
    assert result["screening_failed"] == 1
    assert result["report_incomplete"] is True
    assert set(attempts.values()) == {3}
    with database.SessionLocal() as session:
        failed = session.query(ScreeningQueueItem).filter_by(status="failed").one()
        assert failed.attempts == 3
        assert "simulated timeout" in (failed.last_error or "")
        failed_match = session.query(PaperMatch).filter_by(paper_id=failed.paper_id).one()
        assert failed_match.screened_at is None


def test_server_auth_login_cookie_and_logout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from argon2 import PasswordHasher

    monkeypatch.setenv("XIVPULSE_AUTH_ENABLED", "true")
    monkeypatch.setenv("XIVPULSE_ADMIN_USERNAME", "researcher")
    monkeypatch.setenv("XIVPULSE_ADMIN_PASSWORD_HASH", PasswordHasher().hash("test-secret"))
    monkeypatch.setenv("XIVPULSE_SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("XIVPULSE_SECURE_COOKIES", "false")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").status_code == 401
    login = client.post(
        "/api/auth/login",
        json={"username": "researcher", "password": "test-secret"},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert client.get("/api/settings").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/settings").status_code == 401
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "researcher", "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "researcher", "password": "wrong"},
        ).status_code
        == 429
    )


@pytest.mark.asyncio
async def test_email_delivery_is_idempotent_for_automatic_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import daily_reports

    sent_subjects: list[str] = []

    def fake_deliver(_config: object, message: object) -> None:
        sent_subjects.append(str(message["Subject"]))  # type: ignore[index]

    monkeypatch.setattr(daily_reports, "deliver_message", fake_deliver)
    saved = client.put(
        "/api/settings",
        json={
            "smtp_host": "smtp.test",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "user",
            "smtp_password": "secret",
            "smtp_from": "radar@example.com",
            "email_recipients": ["reader@example.com"],
            "public_base_url": "https://203.0.113.10",
        },
    )
    assert saved.status_code == 200
    assert "secret" not in saved.text
    report_date = date(2026, 8, 14)
    assert client.get(f"/api/reports/daily?date={report_date.isoformat()}").status_code == 200
    first = await daily_reports.send_daily_report_email(
        report_date, delivery_kind="automatic", require_enabled=False
    )
    second = await daily_reports.send_daily_report_email(
        report_date, delivery_kind="automatic", require_enabled=False
    )
    assert first["email_sent"] == 1
    assert second["email_sent"] == 0
    assert second["email_status"] == "duplicate"
    assert len(sent_subjects) == 1
    with database.SessionLocal() as session:
        assert session.query(EmailDelivery).filter_by(status="sent").count() == 1


def test_health_profile_settings_and_dashboard(client: TestClient) -> None:
    paper_id = _insert_paper()

    assert client.get("/api/health").json()["ok"] is True
    profile = client.get("/api/profile")
    assert profile.status_code == 200
    assert profile.json()["topics"]
    profile_payload = profile.json()
    profile_payload["min_relevance"] = 75
    profile_payload["author_watchlist"] = ["A. Author"]
    saved_profile = client.put("/api/profile", json=profile_payload)
    assert saved_profile.status_code == 200
    assert saved_profile.json()["min_relevance"] == 75

    catalog = client.get("/api/directions/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["count"] >= 30
    assert catalog.json()["version"]
    assert all(item["keywords"] for item in catalog.json()["items"])
    assert saved_profile.json()["author_watchlist"] == ["A. Author"]
    assert client.get("/api/profile").json()["author_watchlist"] == ["A. Author"]

    settings = client.put(
        "/api/settings",
        json={"model": "local-test-model", "api_key": "never-return-this"},
    )
    assert settings.status_code == 200
    assert settings.json()["api_key_configured"] is True
    assert "never-return-this" not in settings.text
    assert "never-return-this" not in client.get("/api/settings").text

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["total_papers"] == 1
    assert dashboard["screened_papers"] == 0
    assert dashboard["items"][0]["id"] == paper_id
    assert dashboard["settings"]["schedule_time"] == "13:30"


def test_budget_token_usage_and_method_candidate_review(client: TestClient) -> None:
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        for suffix in ("51", "52"):
            paper = Paper(
                arxiv_id=f"2608.000{suffix}",
                title=f"Candidate method paper {suffix}",
                abstract="A distinct method is evaluated.",
                authors_json=["Researcher"],
                categories_json=["cs.LG"],
                primary_category="cs.LG",
                last_seen_at=datetime.now(UTC),
            )
            session.add(paper)
            session.flush()
            session.add(
                Analysis(
                    paper_id=paper.id,
                    profile_id=profile.id,
                    kind="deep",
                    status="completed",
                    provider="test",
                    model="test",
                    budget_mode="economy",
                    usage_available=True,
                    input_tokens=100,
                    cached_input_tokens=20,
                    output_tokens=30,
                    reasoning_tokens=5,
                    total_tokens=130,
                    payload_json={"methods": ["Counterfactual Policy Repair"]},
                )
            )
        session.commit()

    saved = client.put("/api/settings", json={"analysis_budget_mode": "deep"})
    assert saved.status_code == 200
    assert saved.json()["analysis_budget_mode"] == "deep"
    assert saved.json()["token_usage"]["total_tokens"] == 260
    assert saved.json()["token_usage"]["cached_input_tokens"] == 40
    assert saved.json()["token_usage"]["usage_available_count"] == 2

    catalog = client.get("/api/methods/catalog").json()
    assert catalog["built_in_count"] >= 90
    candidates = client.get("/api/methods/candidates").json()
    assert candidates["token_cost"] == 0
    candidate = next(
        item for item in candidates["items"] if item["name"] == "Counterfactual Policy Repair"
    )
    accepted = client.post(
        "/api/methods/candidates/accept",
        json={"key": candidate["key"], "name": "反事实策略修复"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["custom_count"] == 1
    assert any(
        item["name"] == "反事实策略修复" and item["source"] == "custom"
        for item in client.get("/api/methods/catalog").json()["items"]
    )
    assert client.get("/api/methods/candidates").json()["count"] == 0


def _saved_synthesis_report(report_date: date) -> dict[str, object]:
    return {
        "profile_id": 1,
        "report_date": report_date.isoformat(),
        "title": f"{report_date.isoformat()} 当日研究总结",
        "coverage": {
            "paper_count": 2,
            "candidate_paper_count": 3,
            "analyzed_paper_count": 2,
            "deep_analysis_count": 1,
        },
        "executive_summary": "当天两篇论文围绕可靠机器人策略形成互补证据。",
        "research_situation": {
            "synthesis": "当天正式方向论文集中在机器人策略可靠性。",
            "quality_overview": {
                "lead": "两篇论文分别研究预测与恢复。",
                "paper_findings": [],
                "comparison": "方法互补。",
                "evidence_conclusion": "证据仍有限。",
            },
        },
        "topic_insights": [{"topic": "机器人学习", "paper_count": 2}],
        "highlight_papers": [
            {"paper_id": 1, "title": "Predictive Robot Policy", "summary": "预测失败。"},
            {"paper_id": 2, "title": "Recovering Robot Policy", "summary": "恢复动作。"},
        ],
        "common_methods": [{"name": "模型预测控制", "count": 2}],
        "common_evidence": [{"name": "真实机器人实验", "count": 2}],
        "suggested_reading_queue": [],
    }


@pytest.mark.asyncio
async def test_daily_ai_synthesis_is_persisted_and_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import daily_reports
    from app.services.provider import GenerationResult, TokenUsage
    from app.services.report_synthesis import DailyReportSynthesis, SynthesisThread

    report_date = date(2026, 8, 14)
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        session.add(
            DailyResearchReport(
                profile_id=profile.id,
                report_date=report_date,
                payload_json=_saved_synthesis_report(report_date),
            )
        )
        session.add(AppSetting(key="api_key", value_json="test-only-key"))
        session.commit()

    generated = GenerationResult(
        data=DailyReportSynthesis(
            headline="可靠机器人策略开始连接预测、诊断与恢复",
            overall_assessment=(
                "两篇论文从预测和恢复两个环节处理机器人策略失效，形成了可以横向比较的"
                "闭环路线。当前证据支持优先核查方法和实验，但尚不足以外推到更多平台。"
            )
            * 2,
            core_threads=[
                SynthesisThread(
                    title="预测与恢复闭环",
                    analysis="预测论文提供失效前信号，恢复论文处理失效后的动作选择，两者互补但尚未在统一平台联合验证。",
                    paper_ids=[1, 2],
                )
            ],
            method_comparison=(
                "模型预测控制强调显式状态演化，恢复策略强调执行后的纠偏；二者依赖的"
                "信息和计算位置不同，不能只按成功率排序。还要分别核查建模代价、在线"
                "推理开销、控制器先验和恢复动作空间，避免把系统组件差异误当成算法优势。"
            ),
            evidence_assessment=(
                "当前只有一篇完成全文深读，真实机器人证据的任务和平台覆盖有限，因此"
                "只能支持路线比较，不能支持通用性结论。两篇论文的失败定义和指标口径"
                "也并不完全一致，需要回到原文核查基线、消融和未成功案例。"
            ),
            disagreements_and_risks=(
                "主要风险是长时域误差累积和失败分布覆盖不足；预测准确也不必然意味着"
                "恢复动作有效，需要联合实验验证。此外，传感配置和机器人形态变化可能"
                "造成分布偏移，现有短时域结果无法排除这些外推风险。"
            ),
            research_outlook=(
                "后续应追踪统一失败协议、跨平台实验和预测与恢复联合训练，连续多日重复"
                "出现后才能判断为稳定趋势。尤其要观察是否有共享任务、共享指标和长时域"
                "闭环结果，而不是把一天的论文数量变化直接解释为长期路线转向。"
            ),
            recommended_actions=["优先核查全文深读论文的消融。", "跟踪统一闭环评测。"],
        ),
        transport="responses",
        provider="test-provider",
        model="test-model",
        usage=TokenUsage(input_tokens=800, output_tokens=300, total_tokens=1100),
    )
    fake_generate = AsyncMock(return_value=generated)
    monkeypatch.setattr(daily_reports, "generate_daily_report_synthesis", fake_generate)

    response = client.post(
        "/api/reports/daily/synthesize",
        params={"date": report_date.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()["ai_synthesis"]
    assert payload["status"] == "ready"
    assert payload["core_threads"][0]["paper_ids"] == [1, 2]
    assert payload["usage"]["total_tokens"] == 1100
    assert fake_generate.await_count == 1
    with database.SessionLocal() as session:
        run = session.query(ReportSynthesisRun).one()
        assert run.status == "ready"
        assert run.total_tokens == 1100
    usage = client.get("/api/settings").json()["token_usage"]
    assert usage["by_kind"]["report_synthesis"]["total_tokens"] == 1100
    assert usage["latest"]["kind"] == "report_synthesis"


def test_daily_ai_synthesis_without_key_does_not_call_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import daily_reports

    report_date = date(2026, 8, 13)
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        session.add(
            DailyResearchReport(
                profile_id=profile.id,
                report_date=report_date,
                payload_json=_saved_synthesis_report(report_date),
            )
        )
        session.commit()
    fake_generate = AsyncMock()
    monkeypatch.setattr(daily_reports, "generate_daily_report_synthesis", fake_generate)

    response = client.post(
        "/api/reports/daily/synthesize",
        params={"date": report_date.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["ai_synthesis"]["status"] == "not_configured"
    fake_generate.assert_not_awaited()
    with database.SessionLocal() as session:
        assert session.query(ReportSynthesisRun).count() == 0


def test_analysis_prompt_catalog_and_editable_settings(client: TestClient) -> None:
    catalog_response = client.get("/api/prompts/catalog")
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["default_template_id"] == "balanced-review"
    assert len(catalog["templates"]) == 5
    assert "JSON Schema" in catalog["fixed_prompts"]["deep"]

    settings = client.get("/api/settings").json()
    assert settings["analysis_prompt_template_id"] == "balanced-review"
    assert settings["analysis_prompt_customized"] is False
    custom_screening = "重点检查摘要是否给出了可核查的量化证据，并在证据不足时明确降低相关度。"
    custom_deep = "重点审计实验设计、基线公平性、统计可信度和结论边界，并区分事实、主张与推断。"
    saved = client.put(
        "/api/settings",
        json={
            "analysis_prompt_template_id": "evidence-audit",
            "screening_prompt": custom_screening,
            "deep_prompt": custom_deep,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["analysis_prompt_template_id"] == "evidence-audit"
    assert saved.json()["screening_prompt"] == custom_screening
    assert saved.json()["deep_prompt"] == custom_deep
    assert saved.json()["analysis_prompt_customized"] is True
    assert client.get("/api/settings").json()["deep_prompt"] == custom_deep

    assert (
        client.put(
            "/api/settings", json={"analysis_prompt_template_id": "unknown-template"}
        ).status_code
        == 422
    )
    assert client.put("/api/settings", json={"screening_prompt": "太短"}).status_code == 422


def test_dashboard_direction_stats_ignore_historical_match_labels(
    client: TestClient,
) -> None:
    _insert_paper()
    with database.SessionLocal() as session:
        match = session.query(PaperMatch).one()
        match.matched_topics_json = ["cs.RO", "旧版自由标签", "世界模型与规划"]
        match.matched_keywords_json = ["robot learning", "world model"]
        session.commit()

    dashboard = client.get("/api/dashboard").json()
    paper = dashboard["items"][0]
    profile_directions = {topic["name"] for topic in client.get("/api/profile").json()["topics"]}
    assert set(paper["matched_topics"]) <= profile_directions
    assert {item["topic"] for item in dashboard["topic_stats"]} == set(paper["matched_topics"])
    assert paper["matched_topics"] == []
    assert "cs.RO" not in paper["match_reasons"]
    assert "旧版自由标签" not in paper["match_reasons"]


def test_profile_rejects_duplicate_or_fully_disabled_directions(
    client: TestClient,
) -> None:
    original = client.get("/api/profile").json()

    duplicate = deepcopy(original)
    duplicate["topics"][0]["name"] = "World   Model"
    duplicate["topics"][1]["name"] = "ＷＯＲＬＤ model"
    response = client.put("/api/profile", json=duplicate)
    assert response.status_code == 422
    assert "研究方向名称或别名不能重复" in response.json()["detail"]

    disabled = deepcopy(original)
    for topic in disabled["topics"]:
        topic["enabled"] = False
    response = client.put("/api/profile", json=disabled)
    assert response.status_code == 422
    assert response.json()["detail"] == "至少需要启用一个研究方向"

    unchanged = client.get("/api/profile").json()
    assert [topic["name"] for topic in unchanged["topics"]] == [
        topic["name"] for topic in original["topics"]
    ]
    assert any(topic["enabled"] for topic in unchanged["topics"])


def test_profile_aliases_round_trip_and_reject_cross_direction_conflicts(
    client: TestClient,
) -> None:
    profile = client.get("/api/profile").json()
    profile["topics"][0]["aliases"] = [
        "Embodied Agent",
        "具身 Agent",
        "Embodied Agent",
    ]

    response = client.put("/api/profile", json=profile)

    assert response.status_code == 200
    assert response.json()["topics"][0]["aliases"] == [
        "Embodied Agent",
        "具身 Agent",
    ]

    conflict = response.json()
    conflict["topics"][1]["aliases"] = ["embodied-agent"]
    response = client.put("/api/profile", json=conflict)
    assert response.status_code == 422
    assert "请保留一个正式名称" in response.json()["detail"]

    name_conflict = client.get("/api/profile").json()
    name_conflict["topics"][1]["aliases"] = [name_conflict["topics"][0]["name"]]
    response = client.put("/api/profile", json=name_conflict)
    assert response.status_code == 422


def test_dashboard_exposes_latest_daily_processing_counts(client: TestClient) -> None:
    from app.models import Job

    with database.SessionLocal() as session:
        session.add(
            Job(
                kind="daily",
                status="completed",
                progress=100,
                result_json={"fetched": 612, "screened": 26, "analyzed": 5},
                completed_at=datetime(2026, 8, 13, 6, 3, tzinfo=UTC),
            )
        )
        session.commit()

    summary = client.get("/api/dashboard").json()["processing_summary"]
    assert summary["latest_fetched_count"] == 612
    assert summary["latest_screened_count"] == 26
    assert summary["latest_deep_analyzed_count"] == 5
    assert summary["pending_screening_count"] >= 0
    assert summary["pending_deep_count"] >= 0


def test_profile_round_trips_direction_context_anchors(client: TestClient) -> None:
    profile = client.get("/api/profile").json()
    topic = next(item for item in profile["topics"] if item["name"] == "机器人学习与强化学习")
    assert "robot" in topic["context_keywords"]
    topic["context_keywords"] = ["robot", "humanoid", "robot"]

    response = client.put("/api/profile", json=profile)

    assert response.status_code == 200
    saved = next(
        item for item in response.json()["topics"] if item["name"] == "机器人学习与强化学习"
    )
    assert saved["context_keywords"] == ["robot", "humanoid"]


def test_profile_rejects_legacy_payload_that_would_collapse_directions(
    client: TestClient,
) -> None:
    original = client.get("/api/profile").json()
    response = client.put(
        "/api/profile",
        json={
            "name": original["name"],
            "description": original["description"],
            "positive_keywords": ["robot learning"],
            "negative_keywords": [],
            "categories": ["cs.RO"],
            "author_watchlist": [],
            "min_relevance": 80,
        },
    )
    assert response.status_code == 422
    assert "旧版单方向格式已停用" in response.json()["detail"]
    unchanged = client.get("/api/profile").json()
    assert [topic["name"] for topic in unchanged["topics"]] == [
        topic["name"] for topic in original["topics"]
    ]


def test_paper_filter_detail_and_feedback(client: TestClient) -> None:
    paper_id = _insert_paper(screened=True)
    with database.SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper is not None
        paper.affiliations = ["NVIDIA Research", "Carnegie Mellon University"]
        session.commit()
    listing = client.get("/api/papers", params={"q": "Embodied", "min_score": 80})
    assert listing.status_code == 200
    assert listing.json()["items"][0]["arxiv_id"] == "2608.01234"
    assert listing.json()["items"][0]["notable_organizations"] == ["NVIDIA"]
    assert client.get("/api/papers", params={"q": "A. Author"}).json()["total"] == 1

    detail = client.get(f"/api/papers/{paper_id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "An Embodied AI Paper"
    assert detail.json()["relevance_score"] == 92
    assert detail.json()["affiliations"] == [
        "NVIDIA Research",
        "Carnegie Mellon University",
    ]

    feedback = client.post(
        f"/api/papers/{paper_id}/feedback",
        json={"action": "dismiss", "note": "not for this project"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["action"] == "skip"


def test_paper_library_only_returns_screened_papers_with_pagination(
    client: TestClient,
) -> None:
    first_id = _insert_paper(
        arxiv_id="2608.10001",
        title="Screened Robot Paper One",
        screened=True,
    )
    second_id = _insert_paper(
        arxiv_id="2608.10002",
        title="Screened Robot Paper Two",
        screened=True,
    )
    _insert_paper(
        arxiv_id="2608.10003",
        title="Unscreened Robot Paper",
        screened=False,
    )

    first_page = client.get("/api/papers", params={"limit": 1, "offset": 0})
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert first_page.json()["count"] == 1
    assert first_page.json()["page"] == 1
    assert first_page.json()["pages"] == 2
    assert first_page.json()["has_next"] is True
    assert first_page.json()["scope"] == "screened"

    second_page = client.get("/api/papers", params={"limit": 1, "offset": 1})
    assert second_page.status_code == 200
    assert second_page.json()["page"] == 2
    assert second_page.json()["has_previous"] is True
    assert {first_page.json()["items"][0]["id"], second_page.json()["items"][0]["id"]} == {
        first_id,
        second_id,
    }


def test_structured_feedback_and_reading_state(client: TestClient) -> None:
    paper_id = _insert_paper()
    feedback = client.post(
        f"/api/papers/{paper_id}/feedback",
        json={
            "action": "dislike",
            "rating": 2,
            "note": "Evidence is insufficient",
            "reasons": ["weak_evidence", "no_real_robot", "weak_evidence"],
        },
    )
    assert feedback.status_code == 200
    assert feedback.json()["reasons"] == ["weak_evidence", "no_real_robot"]

    reading = client.patch(
        f"/api/papers/{paper_id}/reading",
        json={
            "state": "reproduce",
            "priority": 5,
            "notes": "Re-run the primary experiment",
            "tags": ["world-model", "benchmark", "World Model", "world_model"],
        },
    )
    assert reading.status_code == 200
    assert reading.json()["state"] == "reproduce"
    assert reading.json()["tags"] == ["world-model", "benchmark"]

    detail = client.get(f"/api/papers/{paper_id}").json()
    assert detail["feedback"]["rating"] == 2
    assert detail["feedback_reasons"] == ["weak_evidence", "no_real_robot"]
    assert detail["reading_state"] == "reproduce"
    assert detail["reading_priority"] == 5
    assert detail["notes"] == "Re-run the primary experiment"
    assert detail["tags"] == ["world-model", "benchmark"]

    replaced = client.post(
        f"/api/papers/{paper_id}/feedback",
        json={"action": "skip", "reasons": ["duplicate"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["reasons"] == ["duplicate"]
    assert client.get(f"/api/papers/{paper_id}").json()["feedback_reasons"] == ["duplicate"]


def test_research_project_crud_and_paper_membership(client: TestClient) -> None:
    paper_id = _insert_paper()
    created = client.post(
        "/api/projects",
        json={
            "name": "World Model Reproduction",
            "description": "Track papers worth reproducing",
            "goal": "Select a baseline",
            "color": "#10B981",
            "due_date": "2026-09-30",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert created.json()["color"] == "#10b981"
    assert created.json()["paper_count"] == 0
    duplicate = client.post("/api/projects", json={"name": "World Model Reproduction"})
    assert duplicate.status_code == 409

    membership = client.post(
        f"/api/projects/{project_id}/papers/{paper_id}",
    )
    assert membership.status_code == 200
    assert membership.json()["created"] is True
    assert membership.json()["reading_state"] == "queued"

    patched_membership = client.patch(
        f"/api/projects/{project_id}/papers/{paper_id}",
        json={
            "reading_state": "reading",
            "priority": 4,
            "notes": "Inspect the ablation",
            "tags": ["ablation", "robotics"],
        },
    )
    assert patched_membership.status_code == 200
    assert patched_membership.json()["created"] is False
    assert patched_membership.json()["paper"]["id"] == paper_id

    updated_membership = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "paper_id": paper_id,
            "reading_state": "read",
            "priority": 5,
            "notes": "Strong baseline",
            "tags": ["baseline"],
        },
    )
    assert updated_membership.status_code == 200
    assert updated_membership.json()["created"] is False

    project = client.get(f"/api/projects/{project_id}")
    assert project.status_code == 200
    assert project.json()["paper_count"] == 1
    assert project.json()["papers"][0]["reading_state"] == "read"
    assert project.json()["papers"][0]["priority"] == 5

    paper = client.get(f"/api/papers/{paper_id}").json()
    assert paper["project_memberships"][0]["project_id"] == project_id
    assert paper["project_memberships"][0]["tags"] == ["baseline"]
    assert paper["projects"] == paper["project_memberships"]

    patched = client.patch(
        f"/api/projects/{project_id}",
        json={"status": "paused", "goal": "Resume after benchmark release"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"
    active_libraries = client.get("/api/projects", params={"status": "active"}).json()
    assert active_libraries["count"] == 1
    assert active_libraries["items"][0]["kind"] == "favorites"

    removed = client.delete(f"/api/projects/{project_id}/papers/{paper_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/projects/{project_id}").json()["paper_count"] == 0

    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_favorites_library_tracks_save_feedback_bidirectionally(client: TestClient) -> None:
    paper_id = _insert_paper()

    libraries = client.get("/api/projects")
    assert libraries.status_code == 200
    favorites = next(item for item in libraries.json()["items"] if item["kind"] == "favorites")
    assert favorites["name"] == "收藏库"
    assert favorites["is_system"] is True
    favorites_id = favorites["id"]

    saved = client.post(f"/api/papers/{paper_id}/feedback", json={"action": "save"})
    assert saved.status_code == 200
    favorites_detail = client.get(f"/api/projects/{favorites_id}").json()
    assert favorites_detail["paper_count"] == 1
    assert favorites_detail["papers"][0]["paper_id"] == paper_id

    unsaved = client.post(f"/api/papers/{paper_id}/feedback", json={"action": "neutral"})
    assert unsaved.status_code == 200
    assert client.get(f"/api/projects/{favorites_id}").json()["paper_count"] == 0

    membership = client.post(f"/api/projects/{favorites_id}/papers/{paper_id}")
    assert membership.status_code == 200
    assert client.get(f"/api/papers/{paper_id}").json()["feedback"]["action"] == "save"

    removed = client.delete(f"/api/projects/{favorites_id}/papers/{paper_id}")
    assert removed.status_code == 204
    assert client.get(f"/api/papers/{paper_id}").json()["feedback"]["action"] == "neutral"

    assert client.patch(f"/api/projects/{favorites_id}", json={"name": "Other"}).status_code == 409
    assert client.delete(f"/api/projects/{favorites_id}").status_code == 409
    with database.SessionLocal() as session:
        stored = session.get(ResearchProject, favorites_id)
        assert stored is not None


def test_favorites_library_backfills_legacy_saved_feedback(client: TestClient) -> None:
    paper_id = _insert_paper()
    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).first()
        assert profile is not None
        session.add(Feedback(paper_id=paper_id, profile_id=profile.id, action="save"))
        session.commit()

    libraries = client.get("/api/projects").json()["items"]
    favorites = next(item for item in libraries if item["kind"] == "favorites")
    detail = client.get(f"/api/projects/{favorites['id']}").json()
    assert detail["paper_count"] == 1
    assert detail["papers"][0]["paper_id"] == paper_id
    assert client.get(f"/api/papers/{paper_id}").status_code == 200


def test_trends_roadmap_and_daily_report_use_requested_date(client: TestClient) -> None:
    paper_id = _insert_paper()
    shanghai = timezone(timedelta(hours=8))
    requested = datetime.now(shanghai).date().isoformat()

    trends = client.get("/api/trends/topics", params={"date": requested})
    assert trends.status_code == 200
    assert trends.json()["as_of"] == requested
    assert trends.json()["daily"]["paper_count"] == 1
    assert isinstance(trends.json()["weekly"]["topics"], list)

    roadmap = client.get(
        "/api/trends/roadmap", params={"date": requested, "days": 30, "max_nodes": 10}
    )
    assert roadmap.status_code == 200
    assert roadmap.json()["as_of"] == requested
    assert roadmap.json()["coverage"]["paper_count"] == 1
    assert roadmap.json()["coverage"]["node_count"] <= 10
    assert isinstance(roadmap.json()["relations"], list)

    report = client.get("/api/reports/daily", params={"date": requested, "max_papers": 5})
    assert report.status_code == 200
    payload = report.json()
    assert payload["report_date"] == requested
    assert payload["coverage"]["paper_count"] == 1
    assert payload["suggested_reading_queue"][0]["paper_id"] == paper_id
    assert payload["persisted"] is True
    assert isinstance(payload["snapshot_id"], int)
    assert datetime.fromisoformat(payload["generated_at"]).utcoffset() == timedelta(0)
    assert datetime.fromisoformat(payload["updated_at"]).utcoffset() == timedelta(0)

    empty_date = "2020-01-01"
    empty = client.get("/api/reports/daily", params={"date": empty_date})
    assert empty.status_code == 200
    assert empty.json()["report_date"] == empty_date
    assert empty.json()["coverage"]["paper_count"] == 0
    assert empty.json()["suggested_reading_queue"] == []
    with database.SessionLocal() as session:
        from app.models import DailyResearchReport

        saved_empty = (
            session.query(DailyResearchReport)
            .filter_by(report_date=datetime.fromisoformat(empty_date).date())
            .one()
        )
        assert saved_empty.payload["coverage"]["paper_count"] == 0


def test_daily_report_snapshot_is_stable_refreshable_and_listed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import daily_reports

    builds: list[int] = []

    def fake_report(*_args: object, **kwargs: object) -> dict[str, object]:
        builds.append(len(builds) + 1)
        report_date = kwargs["report_date"]
        version = builds[-1]
        return {
            "profile_id": 1,
            "report_date": report_date.isoformat(),
            "timezone": kwargs["timezone_name"],
            "title": f"snapshot-v{version}",
            "coverage": {"paper_count": version},
            "suggested_reading_queue": [],
        }

    monkeypatch.setattr(daily_reports, "build_daily_research_report", fake_report)
    report_date = "2026-07-03"

    first = client.get("/api/reports/daily", params={"date": report_date})
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["title"] == "snapshot-v1"
    assert first_payload["persisted"] is True
    assert len(builds) == 1

    cached = client.get("/api/reports/daily", params={"date": report_date})
    assert cached.status_code == 200
    assert cached.json()["title"] == "snapshot-v1"
    assert cached.json()["snapshot_id"] == first_payload["snapshot_id"]
    assert cached.json()["generated_at"] == first_payload["generated_at"]
    assert cached.json()["updated_at"] == first_payload["updated_at"]
    assert len(builds) == 1

    with database.SessionLocal() as session:
        snapshot = (
            session.query(DailyResearchReport)
            .filter_by(report_date=date.fromisoformat(report_date))
            .one()
        )
        payload = snapshot.payload
        payload["ai_synthesis"] = {
            "status": "ready",
            "headline": "已保存的综合研判",
            "overall_assessment": "这份内容必须在确定性日报重建后继续保留。",
            "source_hash": daily_reports.report_source_hash(payload),
            "stale": False,
        }
        snapshot.payload_json = payload
        session.commit()

    refreshed = client.get("/api/reports/daily", params={"date": report_date, "refresh": "true"})
    assert refreshed.status_code == 200
    assert refreshed.json()["title"] == "snapshot-v2"
    assert refreshed.json()["snapshot_id"] == first_payload["snapshot_id"]
    assert refreshed.json()["generated_at"] == first_payload["generated_at"]
    assert refreshed.json()["ai_synthesis"]["headline"] == "已保存的综合研判"
    assert refreshed.json()["ai_synthesis"]["stale"] is True
    assert len(builds) == 2

    regenerated = client.post("/api/reports/daily/regenerate", params={"date": report_date})
    assert regenerated.status_code == 200
    assert regenerated.json()["title"] == "snapshot-v3"
    assert regenerated.json()["snapshot_id"] == first_payload["snapshot_id"]
    assert len(builds) == 3

    history = client.get("/api/reports/daily/history", params={"limit": 1})
    assert history.status_code == 200
    assert history.json()["count"] == 1
    item = history.json()["items"][0]
    assert item["id"] == first_payload["snapshot_id"]
    assert item["report_date"] == report_date
    assert item["title"] == "snapshot-v3"
    assert item["coverage"] == {"paper_count": 3}
    assert item["generated_at"] == first_payload["generated_at"]
    assert datetime.fromisoformat(item["generated_at"]).utcoffset() == timedelta(0)
    assert datetime.fromisoformat(item["updated_at"]).utcoffset() == timedelta(0)


def test_daily_report_without_date_uses_latest_complete_digest(client: TestClient) -> None:
    complete_day = date(2026, 7, 3)
    today = date(2026, 7, 4)
    with database.SessionLocal() as session:
        session.add(
            Digest(
                profile_id=1,
                digest_date=complete_day,
                title="complete",
                status="ready",
            )
        )
        session.commit()

    default_report = client.get("/api/reports/daily")
    explicit_today = client.get("/api/reports/daily", params={"date": today.isoformat()})

    assert default_report.status_code == 200
    assert default_report.json()["report_date"] == complete_day.isoformat()
    assert explicit_today.status_code == 200
    assert explicit_today.json()["report_date"] == today.isoformat()


def test_pipeline_report_stage_uses_configured_timezone_and_upserts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import DailyResearchReport
    from app.services import daily_reports

    report_day = date(2026, 7, 4)
    timezone_calls: list[str] = []
    build_versions: list[int] = []

    def fake_current_date(timezone_name: str) -> date:
        timezone_calls.append(timezone_name)
        return report_day

    def fake_report(*_args: object, **kwargs: object) -> dict[str, object]:
        build_versions.append(len(build_versions) + 1)
        return {
            "profile_id": 1,
            "report_date": kwargs["report_date"].isoformat(),
            "timezone": kwargs["timezone_name"],
            "title": f"pipeline-v{build_versions[-1]}",
            "coverage": {"paper_count": 0},
        }

    monkeypatch.setattr(daily_reports, "current_date_in_timezone", fake_current_date)
    monkeypatch.setattr(daily_reports, "build_daily_research_report", fake_report)

    first = daily_reports.create_daily_report()
    second = daily_reports.create_daily_report()
    assert first["report_snapshot_id"] == second["report_snapshot_id"]
    assert first["report_date"] == report_day.isoformat()
    assert timezone_calls == ["Asia/Shanghai", "Asia/Shanghai"]

    with database.SessionLocal() as session:
        snapshots = session.query(DailyResearchReport).all()
        assert len(snapshots) == 1
        assert snapshots[0].report_date == report_day
        assert snapshots[0].payload["title"] == "pipeline-v2"


def test_digest_status_and_missing_job(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 200
    preview = client.get("/api/digest/preview")
    assert preview.status_code == 200
    assert preview.json()["persisted"] is False
    assert client.get("/api/jobs/does-not-exist").status_code == 404


@pytest.mark.asyncio
async def test_ai_screening_keeps_only_formal_directions_and_preserves_keywords(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Analysis
    from app.schemas import ScreeningResult
    from app.services import paper_analysis
    from app.services import provider as provider_module

    with database.SessionLocal() as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        formal_direction = profile.topics[0].name
        lexical_topic = profile.topics[2]
        stale_legal_direction = profile.topics[4].name
        configured_keyword = lexical_topic.keywords[0]
        paper = Paper(
            arxiv_id="2608.07777",
            title="A Candidate Paper",
            abstract=f"A candidate selected for {configured_keyword} research.",
            authors_json=["Researcher"],
            categories_json=["cs.RO"],
            primary_category="cs.RO",
            last_seen_at=datetime.now(UTC),
        )
        session.add(paper)
        session.flush()
        match = PaperMatch(
            paper_id=paper.id,
            profile_id=profile.id,
            matched_topics_json=[stale_legal_direction, "cs.RO", "自由生成方向"],
            matched_keywords_json=[configured_keyword],
            lexical_score=90,
            semantic_score=0.9,
            relevance_score=90,
        )
        session.add(match)
        session.commit()
        match_id = match.id

    class FakePaperAnalyzer:
        def __init__(self, _adapter: object, **_options: object) -> None:
            pass

        async def screen_with_metadata(self, _paper: Paper, _profile: Profile) -> object:
            return provider_module.GenerationResult(
                data=ScreeningResult(
                    relevant=True,
                    relevance_score=91,
                    reason="AI screening result",
                    matched_topics=[f"  {formal_direction}  ", "cs.RO", "自由生成方向"],
                    key_contributions=["Contribution"],
                    tags=["foundation model", "cs.RO", "free-form tag"],
                ),
                transport="mock",
                provider="test",
                model="test",
            )

    monkeypatch.setattr(paper_analysis, "PaperAnalyzer", FakePaperAnalyzer)

    result = await paper_analysis.analyze_papers(limit=0)
    assert result["screened"] == 1

    with database.SessionLocal() as session:
        stored_match = session.get(PaperMatch, match_id)
        assert stored_match is not None
        assert stored_match.relevance_score == 91
        assert stored_match.matched_topics_json == [formal_direction]
        assert stale_legal_direction not in stored_match.matched_topics_json
        assert stored_match.matched_keywords_json == [configured_keyword]
        analysis = (
            session.query(Analysis)
            .filter_by(paper_id=stored_match.paper_id, kind="screening")
            .one()
        )
        assert analysis.tags_json == ["foundation model", "cs.RO", "free-form tag"]


@pytest.mark.asyncio
async def test_sync_uses_semantic_recall_without_keyword_scoring(
    client: TestClient,
) -> None:
    from app.services.paper_sync import sync_papers
    from app.services.semantic import SemanticMatch

    stages: list[str] = []
    records = [
        PaperDocument(
            arxiv_id="2608.08888",
            version=1,
            title="Vision-Language-Action Robot Learning",
            abstract="This paper contains several configured keywords.",
            authors=["Researcher"],
            categories=["cs.RO"],
            primary_category="cs.RO",
            published_at=datetime.now(UTC),
        ),
        PaperDocument(
            arxiv_id="2608.08889",
            version=1,
            title="A New Cross-Modal Policy Architecture",
            abstract="This abstract deliberately uses new terminology.",
            authors=["Researcher"],
            categories=["cs.RO"],
            primary_category="cs.RO",
            published_at=datetime.now(UTC),
        ),
    ]
    fetch_date = date(2026, 8, 14)

    class FakeArxivClient:
        async def fetch_daily(self, *_args: object, **_kwargs: object) -> list[PaperDocument]:
            stages.append("fetch")
            return records

        async def fetch_html_affiliations(self, _arxiv_ids: list[str]) -> dict[str, list[str]]:
            return {}

    class FakeSemanticScholarClient:
        async def fetch_affiliations(self, _arxiv_ids: list[str]) -> dict[str, list[str]]:
            stages.append("enrich")
            return {}

    def semantic_results(
        _session: object, papers: list[Paper], _topics: object
    ) -> dict[int, SemanticMatch]:
        stages.append("recall")
        assert all(paper.id is not None for paper in papers)
        return {
            paper.id: SemanticMatch(
                score=0.2 if paper.arxiv_id == "2608.08888" else 0.8,
                topic_scores={"VLA": 0.2 if paper.arxiv_id == "2608.08888" else 0.8},
            )
            for paper in papers
        }

    result = await sync_papers(
        session_factory=database.SessionLocal,
        arxiv_client=FakeArxivClient(),
        semantic_scholar=FakeSemanticScholarClient(),
        semantic_matcher=semantic_results,
        date_provider=lambda: fetch_date,
    )
    assert result["fetched"] == 2
    assert result["queued"] == 1
    assert result["fetch_date"] == "2026-08-14"
    assert stages == ["fetch", "recall", "enrich"]
    with database.SessionLocal() as session:
        matches = {
            match.paper.arxiv_id: match
            for match in session.query(PaperMatch).join(PaperMatch.paper).all()
        }
        assert matches["2608.08888"].lexical_score == 0
        assert matches["2608.08888"].semantic_score == pytest.approx(0.2)
        assert matches["2608.08888"].relevance_score == pytest.approx(20)
        assert matches["2608.08889"].semantic_score == pytest.approx(0.8)
        assert matches["2608.08889"].relevance_score == pytest.approx(80)
        queue_item = session.query(ScreeningQueueItem).one()
        assert queue_item.paper.arxiv_id == "2608.08889"
        assert queue_item.report_date == fetch_date


def test_daily_sync_uses_the_latest_complete_utc_date() -> None:
    from app.services.paper_sync import latest_complete_submission_date

    assert latest_complete_submission_date(datetime(2026, 8, 15, 5, 30, tzinfo=UTC)) == date(
        2026, 8, 14
    )


@pytest.mark.asyncio
async def test_sync_uses_fallback_when_semantic_model_is_unavailable(
    client: TestClient,
) -> None:
    from app.services.paper_sync import sync_papers
    from app.services.semantic import SemanticEmbeddingError
    from app.services.state import set_setting

    record = PaperDocument(
        arxiv_id="2608.09991",
        version=1,
        title="Robot Learning with a World Model",
        abstract="We study robot learning with model-based planning for manipulation.",
        authors=["Researcher"],
        categories=["cs.RO"],
        primary_category="cs.RO",
        published_at=datetime.now(UTC),
    )

    class FakeArxivClient:
        async def fetch_daily(self, *_args: object, **_kwargs: object) -> list[PaperDocument]:
            return [record]

        async def fetch_html_affiliations(self, _arxiv_ids: list[str]) -> dict[str, list[str]]:
            return {}

    class FakeSemanticScholarClient:
        async def fetch_affiliations(self, _arxiv_ids: list[str]) -> dict[str, list[str]]:
            return {}

    def unavailable(*_args: object, **_kwargs: object) -> dict[int, object]:
        raise SemanticEmbeddingError("model download unavailable")

    with database.SessionLocal() as session:
        set_setting(session, "author_watchlist", ["Researcher"])
        session.commit()

    result = await sync_papers(
        session_factory=database.SessionLocal,
        arxiv_client=FakeArxivClient(),
        semantic_scholar=FakeSemanticScholarClient(),
        semantic_matcher=unavailable,
        date_provider=lambda: date(2026, 8, 14),
    )

    assert result["fetched"] == 1
    assert result["semantic_degraded"] is True
    assert result["semantic_error"] == "model download unavailable"
    assert result["queued"] == 1
    with database.SessionLocal() as session:
        match = session.query(PaperMatch).one()
        assert match.semantic_score is None
        queue_item = session.query(ScreeningQueueItem).one()
        assert queue_item.semantic_score is None


@pytest.mark.asyncio
async def test_direct_daily_job_is_persisted_and_rejects_overlap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workflow import daily_pipeline

    pipeline = AsyncMock(return_value={"fetched": 3, "analyzed": 1})
    monkeypatch.setattr(daily_pipeline, "execute", pipeline)
    result = await daily_pipeline.run_daily(trigger="test")
    assert result["success"] is True
    assert result["fetched"] == 3
    pipeline.assert_awaited_once()
    assert pipeline.await_args.kwargs["job_id"]
    status_payload = client.get("/api/status").json()
    assert status_payload["latest_job"]["status"] == "completed"

    with database.SessionLocal() as session:
        from app.models import Job

        session.add(Job(kind="sync", status="running", message="active"))
        session.commit()
    blocked = await daily_pipeline.run_daily(trigger="test-overlap")
    assert blocked["accepted"] is False
