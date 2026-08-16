"""Profile, settings, catalog, and provider-test HTTP routes."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..settings import delete_setting, load_runtime_settings, set_setting
from ..database import get_db
from ..models import Analysis, Paper, Topic, utcnow
from ..schemas import ProfileUpdate
from ..services.analytics import current_date_in_timezone
from ..services.daily_reports import persist_daily_report, send_daily_report_email
from ..services.direction_catalog import direction_catalog_payload
from ..services.method_catalog import (
    METHOD_CANDIDATE_MIN_PAPERS,
    METHOD_CANDIDATE_WINDOW_DAYS,
    clean_custom_methods,
    discover_method_candidates,
    method_catalog_payload,
    normalize_method_key,
)
from ..services.prompt_catalog import (
    MAX_EDITABLE_PROMPT_CHARS,
    PROMPT_TEMPLATE_IDS,
    prompt_catalog_payload,
)
from ..services.semantic import SemanticEmbeddingError, embedding_status
from ..services.state import active_profile, clean_string_list
from .presenters import _profile_wire
from .system_views import _settings_wire

router = APIRouter()


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Model provider and API connection.
    provider_name: str | None = None
    model_name: str | None = Field(default=None, max_length=200)
    api_base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=10000)

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


class MethodCandidateDecisionPayload(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodsPayload(BaseModel):
    items: list[CustomMethodPayload] = Field(default_factory=list, max_length=100)


@router.get("/api/profile")
def get_profile(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return _profile_wire(
        active_profile(db),
        settings.deep_analysis_threshold,
        list(settings.watched_authors),
    )


@router.get("/api/directions/catalog")
def get_direction_catalog() -> dict[str, Any]:
    """Return the versioned suggestion catalog without changing the profile."""

    return direction_catalog_payload()


@router.put("/api/profile")
def update_profile(
    payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    if "topics" not in payload:
        raise HTTPException(
            status_code=422,
            detail="保存研究画像必须提交完整 topics；旧版单方向格式已停用，以免覆盖正式研究方向",
        )
    try:
        validated = ProfileUpdate.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        message = str(errors[0].get("msg", "研究画像设置无效")) if errors else "研究画像设置无效"
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        raise HTTPException(status_code=422, detail=message) from exc
    if "min_relevance" in payload:
        threshold = float(payload["min_relevance"])
        if not 0 <= threshold <= 100:
            raise HTTPException(status_code=422, detail="最低相关度必须在 0 到 100 之间")
        set_setting(db, "deep_analysis_threshold", threshold)
    if validated.watched_authors is not None:
        set_setting(
            db,
            "watched_authors",
            clean_string_list(validated.watched_authors),
        )
    for field in ("name", "description", "language", "active"):
        value = getattr(validated, field)
        if value is not None:
            setattr(profile, field, value)
    if validated.topics is not None:
        existing = {topic.id: topic for topic in profile.topics}
        retained: set[int] = set()
        for value in validated.topics:
            topic = existing.get(value.id) if value.id else None
            if topic is None:
                topic = Topic(profile_id=profile.id)
                db.add(topic)
            else:
                retained.add(topic.id)
            for key, item in value.model_dump(exclude={"id"}).items():
                setattr(topic, key, item)
        for topic in profile.topics:
            if topic.id not in retained and topic.id in existing:
                db.delete(topic)
    db.commit()
    db.refresh(profile)
    settings = load_runtime_settings(db)
    return _profile_wire(
        active_profile(db),
        settings.deep_analysis_threshold,
        list(settings.watched_authors),
    )


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


def _method_candidate_records(db: Session, window_days: int) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=max(1, min(window_days, 365)))
    rows = db.execute(
        select(Analysis, Paper)
        .join(Paper, Paper.id == Analysis.paper_id)
        .where(
            Analysis.status == "completed",
            Analysis.created_at >= cutoff,
        )
        .order_by(desc(Analysis.created_at))
    ).all()
    # Prefer the newest method-bearing analysis for a paper. Screening usually
    # has no methods, while deep analysis provides the structured source.
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for analysis, paper in rows:
        methods = analysis.payload.get("methods", [])
        if paper.id in seen or not isinstance(methods, list) or not methods:
            continue
        seen.add(paper.id)
        result.append(
            {
                "paper_id": paper.id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "methods": methods,
            }
        )
    return result


@router.get("/api/methods/catalog")
def methods_catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return method_catalog_payload(clean_custom_methods(list(settings.custom_methods)))


@router.get("/api/methods/candidates")
def method_candidates(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    window_days = METHOD_CANDIDATE_WINDOW_DAYS
    min_papers = METHOD_CANDIDATE_MIN_PAPERS
    custom = clean_custom_methods(list(settings.custom_methods))
    dismissed = list(settings.dismissed_method_candidates)
    candidates = discover_method_candidates(
        _method_candidate_records(db, window_days),
        custom_values=custom,
        dismissed=dismissed,
        min_papers=min_papers,
    )
    return {
        "window_days": window_days,
        "min_papers": max(2, min_papers),
        "count": len(candidates),
        "items": candidates,
        "automatic_accept": False,
        "token_cost": 0,
    }


@router.post("/api/methods/candidates/accept")
def accept_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    candidates = method_candidates(db)["items"]
    candidate = next(
        (item for item in candidates if item["key"] == normalize_method_key(payload.key)),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="该候选已不存在或不再满足重复门槛")
    name = str(payload.name or candidate["name"]).strip()
    custom = clean_custom_methods(list(settings.custom_methods))
    if normalize_method_key(name) in {normalize_method_key(item["name"]) for item in custom}:
        raise HTTPException(status_code=409, detail="自定义方法中已经存在同名标签")
    aliases = clean_string_list(
        [candidate["name"], *payload.aliases] if name != candidate["name"] else payload.aliases,
        limit=20,
    )
    custom.append({"name": name, "aliases": aliases})
    set_setting(db, "custom_methods", custom)
    db.commit()
    return method_catalog_payload(custom)


@router.post("/api/methods/candidates/dismiss")
def dismiss_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    dismissed = list(settings.dismissed_method_candidates)
    key = normalize_method_key(payload.key)
    if key and key not in {normalize_method_key(item) for item in dismissed}:
        dismissed.append(key)
    set_setting(db, "dismissed_method_candidates", dismissed)
    db.commit()
    return {"ok": True, "dismissed_key": key}


@router.put("/api/methods/custom")
def replace_custom_methods(
    payload: CustomMethodsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    custom = clean_custom_methods([item.model_dump() for item in payload.items])
    set_setting(db, "custom_methods", custom)
    db.commit()
    return method_catalog_payload(custom)


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
