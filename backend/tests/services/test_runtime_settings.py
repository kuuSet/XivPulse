"""Runtime settings and database seed tests."""

from __future__ import annotations

import json

import app.settings.process as settings_module
import pytest
from app.database import init_db, make_engine
from app.models import Profile
from app.settings import ProviderSettings, load_runtime_settings, set_setting
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session


def _provider_settings(**overrides: object) -> ProviderSettings:
    values: dict[str, object] = {
        "provider_name": "test-provider",
        "api_base_url": "https://provider.test/v1",
        "model_name": "test-model",
        "api_protocol": "responses",
        "api_key": "test-secret",
    }
    values.update(overrides)
    return ProviderSettings(**values)  # type: ignore[arg-type]


def test_seed_is_idempotent_and_provider_public_dict_never_exposes_secret() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    init_db(engine)
    with Session(engine) as session:
        profiles = list(session.scalars(select(Profile)).all())
        assert len(profiles) == 1
        assert [topic.name for topic in profiles[0].topics] == [
            "具身智能与通用机器人",
            "视觉-语言-动作模型",
            "大语言模型与基础模型",
            "AI 智能体与工具使用",
            "机器学习系统与基础设施",
            "高效模型推理与服务",
        ]
        settings = load_runtime_settings(session)
        assert settings.prefilter_batch_size == 30
        assert settings.deep_analysis_limit == 0
        assert settings.deep_analysis_threshold == 90
        assert settings.providers.prefilter.model_name == "gpt-4o-mini"
        assert settings.providers.synthesis.model_name == "gpt-4o-mini"

    status = _provider_settings(api_key="do-not-leak").public_dict()
    assert status["api_key_configured"] is True
    assert "do-not-leak" not in json.dumps(status)
    assert "api_key" not in status
    codex_status = _provider_settings(model_source="codex", api_key=None).public_dict()
    assert codex_status["api_key_configured"] is False
    assert _provider_settings(model_source="codex", api_key=None).configured is True


def test_invalid_settings_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XIVPULSE_API_TIMEOUT_SECONDS", "601")
    with pytest.raises(ValidationError, match="api_timeout_seconds"):
        settings_module.ProcessSettings()

    monkeypatch.delenv("XIVPULSE_API_TIMEOUT_SECONDS")
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        set_setting(session, "arxiv_fetch_limit", 1001)
        session.commit()
        with pytest.raises(ValidationError, match="arxiv_fetch_limit"):
            load_runtime_settings(session)


def test_unknown_database_settings_are_rejected() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        set_setting(session, "providers", {"screening": {"unknown": True}})
        session.commit()
        with pytest.raises(ValidationError, match="unknown"):
            load_runtime_settings(session)

        set_setting(session, "providers", {})
        set_setting(session, "unknown", True)
        session.commit()
        with pytest.raises(ValidationError, match="unknown"):
            load_runtime_settings(session)


def test_runtime_secrets_are_hidden_from_repr_and_serialization() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        set_setting(session, "smtp_password", "smtp-secret")
        session.commit()
        settings = load_runtime_settings(session)

    assert "smtp-secret" not in repr(settings)
    assert "smtp-secret" not in json.dumps(settings.model_dump(), default=str)


def test_runtime_settings_use_environment_then_database_then_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine) as session:
        set_setting(session, "schedule_time", "08:00")
        set_setting(session, "arxiv_fetch_limit", 250)
        set_setting(
            session,
            "providers",
            {
                "screening": {
                    "api_base_url": "https://database.test/v1",
                    "model_name": "database-model",
                    "api_key": "database-secret",
                },
                "deep": {
                    "model_source": "codex",
                    "codex_model": "gpt-5.6-terra",
                    "codex_reasoning_effort": "high",
                },
            },
        )
        session.commit()

        monkeypatch.setenv("XIVPULSE_SCHEDULE_TIME", "09:15")
        monkeypatch.setenv("XIVPULSE_MODEL_NAME", "environment-model")
        monkeypatch.setenv("XIVPULSE_SMTP_HOST", "smtp.environment.test")
        monkeypatch.setenv(
            "XIVPULSE_EMAIL_RECIPIENTS",
            '["first@example.com", "second@example.com"]',
        )
        process = settings_module.ProcessSettings()
        settings = load_runtime_settings(session, process=process)
        monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
        settings_with_environment_key = load_runtime_settings(session, process=process)

    assert settings.schedule_time == "09:15"
    assert settings.arxiv_fetch_limit == 250
    assert settings.providers.screening.api_base_url == "https://database.test/v1"
    assert settings.providers.screening.model_name == "environment-model"
    assert settings.providers.screening.api_key == "database-secret"
    assert settings.providers.deep.model_source == "codex"
    assert settings.providers.deep.codex_model == "gpt-5.6-terra"
    assert settings.providers.deep.codex_reasoning_effort == "high"
    assert settings_with_environment_key.providers.screening.api_key == "environment-secret"
    assert settings.smtp_host == "smtp.environment.test"
    assert settings.email_recipients == (
        "first@example.com",
        "second@example.com",
    )
