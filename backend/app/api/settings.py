"""Application settings and connection-test HTTP routes."""

from __future__ import annotations

import re
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..modules.analysis.prompts import (
    MAX_EDITABLE_PROMPT_CHARS,
    PROMPT_TEMPLATE_IDS,
    prompt_catalog_payload,
)
from ..profile import active_profile
from ..services.provider_factory import create_model_adapter
from ..services.reporting.records import current_date_in_timezone
from ..services.reporting.service import persist_daily_report, send_daily_report_email
from ..settings import (
    ModelStage,
    ProviderOverride,
    delete_setting,
    load_runtime_settings,
    set_setting,
    validate_timezone_name,
)
from ..settings.store import load_setting_values
from .system_views import _settings_wire

router = APIRouter()


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Independent model connection for each pipeline stage.
    providers: dict[ModelStage, ProviderOverride] | None = None

    # Interface display preferences.
    ui_font_size_pt: float | None = Field(default=None, ge=10.0, le=16.0, multiple_of=0.5)

    # Daily job schedule.
    schedule_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=100)

    # arXiv synchronization limits.
    arxiv_fetch_limit: int | None = Field(default=None, ge=1, le=1000)

    # Number of paper titles sent in one prefilter request.
    prefilter_batch_size: int | None = Field(default=None, ge=1, le=500)

    # Full-text analysis selection.
    deep_analysis_limit: int | None = Field(default=None, ge=0, le=100)
    deep_analysis_threshold: float | None = Field(default=None, ge=0, le=100)

    # Model analysis and daily synthesis.
    analysis_budget: Literal["economy", "balanced", "deep"] | None = None
    daily_synthesis_enabled: bool | None = None

    # Analysis prompt selection and overrides.
    prompt_template_id: str | None = Field(default=None, max_length=80)
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

    @field_validator("schedule_timezone")
    @classmethod
    def validate_schedule_timezone(cls, value: str | None) -> str | None:
        return validate_timezone_name(value) if value is not None else None

    @field_validator("deep_analysis_prompt")
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

    stage: ModelStage


@router.get("/api/settings")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _settings_wire(db)


@router.put("/api/settings")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    provider_updates = values.pop("providers", None)
    if provider_updates:
        providers = dict(load_setting_values(db).get("providers", {}))
        for stage, update in provider_updates.items():
            provider = dict(providers.get(stage, {}))
            if update.get("api_key") == "":
                provider.pop("api_key", None)
                update.pop("api_key")
            provider.update(update)
            providers[stage] = provider
        set_setting(db, "providers", providers)
    for key, value in values.items():
        if key == "smtp_password" and value == "":
            delete_setting(db, key)
        elif key != "smtp_password" or value:
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
    provider_settings = settings.providers.for_stage(payload.stage)
    return (await create_model_adapter(provider_settings).test_connection()).model_dump()


@router.post("/api/settings/email/test")
async def test_email_settings(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
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
        session_factory=request.app.state.session_factory,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}
