"""Application settings and connection-test HTTP routes."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..settings import delete_setting, load_runtime_settings, set_setting
from ..services.reporting.records import current_date_in_timezone
from ..services.reporting.service import persist_daily_report, send_daily_report_email
from ..services.prompt_catalog import (
    MAX_EDITABLE_PROMPT_CHARS,
    PROMPT_TEMPLATE_IDS,
    prompt_catalog_payload,
)
from ..services.semantic.embedding import SemanticEmbeddingError, embedding_status
from ..services.state import active_profile
from .system_views import _settings_wire

router = APIRouter()


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Model provider and API connection.
    provider_name: str | None = None
    model_name: str | None = Field(default=None, max_length=200)
    api_base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=10000)

    # Interface display preferences.
    ui_font_size_pt: float | None = Field(
        default=None, ge=10.0, le=16.0, multiple_of=0.5
    )

    # Daily job schedule.
    schedule_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=100)

    # arXiv synchronization limits.
    arxiv_fetch_limit: int | None = Field(default=None, ge=1, le=1000)

    # Candidate screening and semantic recall.
    screening_batch_size: int | None = Field(default=None, ge=0, le=500)
    semantic_recall_mode: Literal["high", "balanced", "precise"] | None = None

    # Full-text analysis selection.
    deep_analysis_limit: int | None = Field(default=None, ge=0, le=100)
    deep_analysis_threshold: float | None = Field(default=None, ge=0, le=100)

    # Model analysis and daily synthesis.
    analysis_budget: Literal["economy", "balanced", "deep"] | None = None
    daily_synthesis_enabled: bool | None = None

    # Analysis prompt selection and overrides.
    prompt_template_id: str | None = Field(default=None, max_length=80)
    screening_prompt: str | None = Field(
        default=None, min_length=20, max_length=MAX_EDITABLE_PROMPT_CHARS
    )
    deep_analysis_prompt: str | None = Field(
        default=None, min_length=20, max_length=MAX_EDITABLE_PROMPT_CHARS
    )

    # Email delivery controls.
    email_enabled: bool | None = None
    email_recipients: list[str] | None = Field(default=None, max_length=50)

    # SMTP connection and sender identity.
    smtp_host: str | None = Field(default=None, max_length=500)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] | None = None
    smtp_username: str | None = Field(default=None, max_length=500)
    smtp_password: str | None = Field(default=None, max_length=10000)
    smtp_sender: str | None = Field(default=None, max_length=500)

    # Public application URL included in outbound email.
    public_url: str | None = Field(default=None, max_length=2000)

    @field_validator("prompt_template_id")
    @classmethod
    def validate_prompt_template(cls, value: str | None) -> str | None:
        if value is not None and value not in PROMPT_TEMPLATE_IDS:
            raise ValueError("未知的论文分析提示词模板")
        return value

    @field_validator("screening_prompt", "deep_analysis_prompt")
    @classmethod
    def normalize_editable_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if len(cleaned) < 20:
            raise ValueError("论文分析提示词至少需要 20 个字符")
        return cleaned

    @field_validator("email_recipients")
    @classmethod
    def normalize_recipients(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        for raw in value:
            address = str(raw).strip()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
                raise ValueError(f"无效的收件邮箱：{address}")
            if address.casefold() not in {item.casefold() for item in result}:
                result.append(address)
        return result


class ProviderTestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Model provider and API connection to test.
    provider_name: str | None = None
    model_name: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None


@router.get("/api/settings")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _settings_wire(db)


@router.put("/api/settings")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    for key, value in values.items():
        if key in {"api_key", "smtp_password"} and value == "":
            delete_setting(db, key)
        elif key not in {"api_key", "smtp_password"} or value:
            set_setting(db, key, value)
    db.commit()
    return _settings_wire(db)


@router.get("/api/prompts/catalog")
def prompt_catalog() -> dict[str, Any]:
    return prompt_catalog_payload()


@router.post("/api/settings/test")
async def test_provider(
    payload: ProviderTestPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    key = payload.api_key or settings.provider.api_key
    api_base_url = (payload.api_base_url or settings.provider.api_base_url).rstrip("/")
    model_name = payload.model_name or settings.provider.model_name
    if not key:
        return {
            "ok": True,
            "message": "未配置 API Key，当前将使用本地演示分析",
            "provider_name": "mock",
            "model": "mock",
            "transport": "mock",
        }
    try:
        from ..services.provider import OpenAICompatibleAdapter

        provider_settings = settings.provider.model_copy(
            update={
                "provider_name": payload.provider_name
                or settings.provider.provider_name
                or "openai-compatible",
                "api_base_url": api_base_url,
                "model_name": model_name,
                "api_key": key,
            }
        )
        result = await OpenAICompatibleAdapter(provider_settings).test_connection()
        return result.model_dump()
    except Exception as exc:
        return {
            "ok": False,
            "message": f"连接失败：{exc}",
            "provider_name": payload.provider_name or "openai-compatible",
            "model": model_name,
            "transport": "http",
        }


@router.post("/api/settings/embedding/test")
async def test_embedding_model() -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(embedding_status, warm=True)
        return {"ok": True, **result}
    except (SemanticEmbeddingError, OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc), **embedding_status()}


@router.post("/api/settings/email/test")
async def test_email_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = active_profile(db)
    settings = load_runtime_settings(db)
    selected_date = current_date_in_timezone(settings.schedule_timezone)
    snapshot = persist_daily_report(
        db,
        profile.id,
        report_date=selected_date,
        timezone_name=settings.schedule_timezone,
        refresh=False,
    )
    db.commit()
    result = await send_daily_report_email(
        snapshot.report_date,
        delivery_kind=f"test-{uuid4().hex[:12]}",
        require_enabled=False,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}
