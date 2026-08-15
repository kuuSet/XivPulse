"""FastAPI application and orchestration for the local paper radar."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import threading
import unicodedata
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import Integer, desc, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import get_config, provider_status
from .database import SessionLocal, get_db, init_db, session_scope
from .env import xivpulse_env
from .models import (
    Analysis,
    AppSetting,
    DailyResearchReport,
    Digest,
    EmailDelivery,
    Feedback,
    FeedbackReason,
    Job,
    Paper,
    PaperMatch,
    PaperReadingState,
    Profile,
    ProjectPaper,
    ReportSynthesisRun,
    ResearchProject,
    ScreeningQueueItem,
    Topic,
    utcnow,
)
from .schemas import ProfileUpdate
from .services.analytics import (
    build_daily_research_report,
    build_research_roadmap,
    build_topic_trends,
    current_date_in_timezone,
)
from .services.arxiv import ArxivClient, build_profile_query, upsert_papers
from .services.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    auth_config,
    clear_login_failures,
    issue_session,
    login_retry_after,
    record_login_failure,
    session_username,
    verify_password,
)
from .services.direction_catalog import direction_catalog_payload
from .services.direction_matching import (
    canonical_requested_direction_names,
    contains_term,
    direction_evidence,
    matched_direction_names,
)
from .services.direction_taxonomy import (
    direction_taxonomy_fingerprint,
    has_current_direction_attribution,
    stamp_direction_attribution,
)
from .services.mailer import build_daily_message, deliver_message, smtp_config
from .services.method_catalog import (
    METHOD_CANDIDATE_MIN_PAPERS,
    METHOD_CANDIDATE_WINDOW_DAYS,
    discover_method_candidates,
    method_catalog_payload,
    normalize_method_key,
)
from .services.organizations import paper_organizations
from .services.prompt_catalog import (
    MAX_EDITABLE_PROMPT_CHARS,
    PROMPT_TEMPLATE_IDS,
    prompt_catalog_payload,
    resolve_prompt_settings,
)
from .services.recommender import RecommendationEngine
from .services.report_synthesis import (
    generate_daily_report_synthesis,
    report_source_hash,
    synthesis_has_enough_data,
)
from .services.semantic import (
    LEXICAL_FLOORS,
    RECALL_THRESHOLDS,
    SemanticEmbeddingError,
    embedding_status,
    recall_mode,
)
from .services.semantic import (
    MODEL_ID as SEMANTIC_MODEL_ID,
)
from .services.semantic import (
    is_candidate as is_semantic_candidate,
)
from .services.semantic import (
    match_papers as semantic_match_papers,
)

LOG = logging.getLogger("xivpulse")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
PIPELINE_LOCK_PATH = PROJECT_ROOT / "data" / "pipeline.lock"
RUNNING_STATUSES = ("queued", "running")
SCREENING_LEXICAL_THRESHOLD = 50
SCREENING_RETRY_DELAYS = (5.0, 30.0)
FAVORITES_LIBRARY_KIND = "favorites"
FAVORITES_LIBRARY_NAME = "收藏库"
FAVORITES_LIBRARY_COLOR = "#b54747"
_initialization_lock = threading.Lock()
_initialized = False
_job_lock = threading.Lock()
_active_job_id: str | None = None
_embedding_backfill_started = False


def _acquire_pipeline_lock(token: str) -> bool:
    """Atomically coordinate web and scheduled jobs across local processes."""

    PIPELINE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(
                PIPELINE_LOCK_PATH,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                age = datetime.now().timestamp() - PIPELINE_LOCK_PATH.stat().st_mtime
                if age <= timedelta(hours=2).total_seconds():
                    return False
                PIPELINE_LOCK_PATH.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{token}\n{os.getpid()}\n{socket.gethostname()}\n")
        return True
    return False


def _release_pipeline_lock(token: str) -> None:
    try:
        owner = PIPELINE_LOCK_PATH.read_text(encoding="utf-8").splitlines()[0]
        if owner == token:
            PIPELINE_LOCK_PATH.unlink()
    except (FileNotFoundError, OSError, UnicodeError):
        return


class SettingsPayload(BaseModel):
    provider: str | None = None
    provider_name: str | None = None
    base_url: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=10000)
    refresh_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    max_candidates: int | None = Field(default=None, ge=1, le=1000)
    max_results: int | None = Field(default=None, ge=1, le=1000)
    deep_analysis_limit: int | None = Field(default=None, ge=0, le=100)
    deep_analysis_top_n: int | None = Field(default=None, ge=0, le=100)
    deep_relevance_threshold: float | None = Field(default=None, ge=0, le=100)
    screen_top_n: int | None = Field(default=None, ge=0, le=500)
    screen_batch_size: int | None = Field(default=None, ge=0, le=500)
    semantic_recall_mode: Literal["high", "balanced", "precise"] | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    analysis_budget_mode: Literal["economy", "balanced", "deep"] | None = None
    daily_ai_synthesis_enabled: bool | None = None
    analysis_prompt_template_id: str | None = Field(default=None, max_length=80)
    screening_prompt: str | None = Field(
        default=None, min_length=20, max_length=MAX_EDITABLE_PROMPT_CHARS
    )
    deep_prompt: str | None = Field(
        default=None, min_length=20, max_length=MAX_EDITABLE_PROMPT_CHARS
    )
    email_enabled: bool | None = None
    smtp_host: str | None = Field(default=None, max_length=500)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] | None = None
    smtp_username: str | None = Field(default=None, max_length=500)
    smtp_password: str | None = Field(default=None, max_length=10000)
    smtp_from: str | None = Field(default=None, max_length=500)
    email_recipients: list[str] | None = Field(default=None, max_length=50)
    public_base_url: str | None = Field(default=None, max_length=2000)

    @field_validator("analysis_prompt_template_id")
    @classmethod
    def validate_prompt_template(cls, value: str | None) -> str | None:
        if value is not None and value not in PROMPT_TEMPLATE_IDS:
            raise ValueError("未知的论文分析提示词模板")
        return value

    @field_validator("screening_prompt", "deep_prompt")
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
    provider: str | None = None
    provider_name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=10000)


class MethodCandidateDecisionPayload(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodsPayload(BaseModel):
    items: list[CustomMethodPayload] = Field(default_factory=list, max_length=100)


class FeedbackPayload(BaseModel):
    action: Literal["neutral", "save", "like", "dislike", "skip", "dismiss", "read"] = "neutral"
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=5000)
    reasons: list[
        Literal[
            "not_relevant",
            "incremental",
            "weak_evidence",
            "no_real_robot",
            "no_code",
            "duplicate",
            "poor_writing",
            "topic_pause",
        ]
    ] = Field(default_factory=list, max_length=8)


ReadingStateValue = Literal["unread", "queued", "reading", "read", "reproduce", "cited", "archived"]
ProjectStatusValue = Literal["active", "paused", "completed"]


class ReadingPayload(BaseModel):
    state: ReadingStateValue | None = None
    reading_state: ReadingStateValue | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=50)


class ProjectCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20000)
    status: ProjectStatusValue = "active"
    color: str = Field(default="#2563eb", pattern=r"^#[0-9A-Fa-f]{6}$")
    goal: str = Field(default="", max_length=20000)
    due_date: date | None = None


class ProjectUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20000)
    status: ProjectStatusValue | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    goal: str | None = Field(default=None, max_length=20000)
    due_date: date | None = None


class ProjectPaperPayload(BaseModel):
    paper_id: int = Field(gt=0)
    reading_state: ReadingStateValue = "queued"
    priority: int = Field(default=3, ge=1, le=5)
    notes: str = Field(default="", max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=50)


class ProjectPaperUpdatePayload(BaseModel):
    reading_state: ReadingStateValue | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=50)


def _backfill_existing_semantic_embeddings() -> None:
    """Populate local vectors for existing papers without invoking AI analysis."""

    try:
        with SessionLocal() as db:
            profile = _active_profile(db)
            paper_ids = list(db.scalars(select(Paper.id).order_by(Paper.id)).all())
            profile_id = profile.id
        total = len(paper_ids)
        processed = 0
        for start in range(0, total, 128):
            selected_ids = paper_ids[start : start + 128]
            with session_scope() as db:
                profile = _active_profile(db)
                papers = list(
                    db.scalars(select(Paper).where(Paper.id.in_(selected_ids))).all()
                )
                results = semantic_match_papers(db, papers, list(profile.topics))
                direction_names = _enabled_direction_names(profile)
                matches = db.scalars(
                    select(PaperMatch).where(
                        PaperMatch.profile_id == profile_id,
                        PaperMatch.paper_id.in_(selected_ids),
                    )
                ).all()
                for match in matches:
                    semantic = results.get(match.paper_id)
                    if semantic is not None:
                        match.semantic_score = semantic.score
                        match.semantic_topics_json = semantic.topic_scores
                        match.semantic_model = SEMANTIC_MODEL_ID
                    match.matched_topics_json = _canonical_directions(
                        match.matched_topics, direction_names
                    )
                processed += len(selected_ids)
                _set_setting(
                    db,
                    "embedding_backfill",
                    {"status": "running", "processed": processed, "total": total},
                )
        with session_scope() as db:
            _set_setting(
                db,
                "embedding_backfill",
                {"status": "completed", "processed": processed, "total": total},
            )
    except Exception as exc:
        LOG.exception("Existing-paper semantic embedding backfill failed")
        try:
            with session_scope() as db:
                _set_setting(
                    db,
                    "embedding_backfill",
                    {"status": "failed", "error": str(exc)[:1000]},
                )
        except Exception:
            LOG.exception("Failed to persist embedding backfill error")


def initialize_application() -> None:
    global _embedding_backfill_started, _initialized
    if _initialized:
        return
    with _initialization_lock:
        if _initialized:
            return
        auth_config()
        init_db()
        with session_scope() as db:
            stale_before = utcnow() - timedelta(hours=2)
            for job in db.scalars(select(Job).where(Job.status.in_(RUNNING_STATUSES))).all():
                heartbeat = _as_utc(job.started_at or job.created_at)
                if heartbeat and heartbeat < stale_before:
                    job.status = "failed"
                    job.error = "应用退出后遗留的任务已自动关闭"
                    job.message = "任务超时"
                    job.completed_at = utcnow()
        _initialized = True
        backfill_enabled = str(xivpulse_env("EMBEDDING_BACKFILL", "true")).strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if backfill_enabled and not _embedding_backfill_started:
            _embedding_backfill_started = True
            threading.Thread(
                target=_backfill_existing_semantic_embeddings,
                name="semantic-embedding-backfill",
                daemon=True,
            ).start()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_application()
    yield


app = FastAPI(
    title="XivPulse",
    description="本机 arXiv 论文筛选、总结与推荐服务",
    version="0.1.0",
    lifespan=lifespan,
)

trusted_hosts = [
    value.strip()
    for value in str(xivpulse_env("TRUSTED_HOSTS", "")).split(",")
    if value.strip()
]
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


def _source_ip(request: Request) -> str:
    config = auth_config()
    if config.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client is not None else "unknown"


@app.middleware("http")
async def require_server_auth(request: Request, call_next: Any) -> Response:
    config = auth_config()
    path = request.url.path
    public_api = {
        "/api/health",
        "/api/auth/session",
        "/api/auth/login",
        "/api/auth/logout",
    }
    if config.enabled and path.startswith("/api/") and path not in public_api:
        username = session_username(config, request.cookies.get(SESSION_COOKIE))
        if username is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        request.state.auth_user = username
    return await call_next(request)


@app.get("/api/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    config = auth_config()
    username = session_username(config, request.cookies.get(SESSION_COOKIE))
    return {
        "enabled": config.enabled,
        "authenticated": not config.enabled or username is not None,
        "username": username,
    }


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    config = auth_config()
    if not config.enabled:
        return {"enabled": False, "authenticated": True, "username": "local"}
    source_ip = _source_ip(request)
    retry_after = login_retry_after(source_ip)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    if not verify_password(config, payload.username, payload.password):
        retry_after = record_login_failure(source_ip)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(status_code=401, detail="用户名或密码错误", headers=headers)
    clear_login_failures(source_ip)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(config),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )
    return {"enabled": True, "authenticated": True, "username": config.username}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    config = auth_config()
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )
    return {"enabled": config.enabled, "authenticated": False}


def _setting_map(db: Session) -> dict[str, Any]:
    return {item.key: item.value for item in db.scalars(select(AppSetting)).all()}


def _set_setting(db: Session, key: str, value: Any) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=value))
    else:
        setting.value = value


def _configured_provider_config(settings: dict[str, Any]):
    """Overlay database provider settings without exposing stored credentials."""

    config = get_config()
    return replace(
        config,
        provider_name=str(settings.get("provider_name") or config.provider_name),
        base_url=str(settings.get("base_url") or config.base_url),
        model=str(settings.get("model") or config.model),
        api_key=settings.get("api_key") or config.api_key,
        config_source=(
            "database"
            if any(key in settings for key in ("base_url", "model", "api_key"))
            else config.config_source
        ),
        key_source="database" if settings.get("api_key") else config.key_source,
    )


def _active_profile(db: Session) -> Profile:
    profile = db.scalar(
        select(Profile)
        .options(selectinload(Profile.topics))
        .where(Profile.active.is_(True))
        .order_by(Profile.id)
    )
    if profile is None:
        profile = db.scalar(
            select(Profile).options(selectinload(Profile.topics)).order_by(Profile.id)
        )
    if profile is None:
        raise HTTPException(status_code=500, detail="研究画像尚未初始化")
    return profile


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _latest_analysis(
    paper: Paper,
    kind: str | None = None,
    *,
    profile_id: int | None = None,
) -> Analysis | None:
    analyses = [
        item
        for item in paper.analyses
        if (kind is None or item.kind == kind)
        and (profile_id is None or item.profile_id == profile_id)
    ]
    return max(
        analyses,
        key=lambda item: _as_utc(item.created_at) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )


def _clean_string_list(value: Any, *, limit: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _clean_custom_methods(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = normalize_method_key(name)
        if not name or not key or key in seen:
            continue
        aliases = _clean_string_list(item.get("aliases", []), limit=20)
        result.append({"name": name, "aliases": aliases})
        seen.add(key)
    return result


def _direction_key(value: object) -> str:
    """Normalize exact names and aliases without semantic fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def _enabled_direction_names(profile: Profile) -> dict[str, str]:
    result: dict[str, str] = {}
    for topic in profile.topics:
        if not topic.enabled:
            continue
        for name in (topic.name, *topic.aliases):
            if key := _direction_key(name):
                result[key] = topic.name
    return result


def _canonical_directions(values: Any, names_by_key: dict[str, str]) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        key = _direction_key(value)
        canonical = names_by_key.get(key)
        if canonical is not None and key not in seen:
            seen.add(key)
            result.append(canonical)
    return result


def _configured_keyword_directions(paper: Paper, topics: list[Topic]) -> list[str]:
    """Derive direction attribution from current configured keyword evidence."""

    text = f"{paper.title}\n{paper.abstract}"
    return matched_direction_names(text, topics)


def _clean_tags(value: Any, *, limit: int = 50) -> list[str]:
    """Keep the first spelling while collapsing visually equivalent user tags."""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = unicodedata.normalize("NFKC", item).strip()
        key = re.sub(r"[\s_-]+", "", cleaned).casefold()
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _profile_wire(
    profile: Profile,
    min_relevance: float = 80.0,
    author_watchlist: list[str] | None = None,
) -> dict[str, Any]:
    enabled = [topic for topic in profile.topics if topic.enabled]
    positive = list(dict.fromkeys(keyword for topic in enabled for keyword in topic.keywords))
    negative = list(
        dict.fromkeys(keyword for topic in enabled for keyword in topic.negative_keywords)
    )
    categories = list(
        dict.fromkeys(category for topic in enabled for category in topic.arxiv_categories)
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "language": profile.language,
        "active": profile.active,
        "positive_keywords": positive,
        "negative_keywords": negative,
        "categories": categories,
        "author_watchlist": author_watchlist or [],
        "min_relevance": min_relevance,
        "topics": [
            {
                "id": topic.id,
                "profile_id": topic.profile_id,
                "name": topic.name,
                "aliases": topic.aliases,
                "query": topic.query,
                "arxiv_categories": topic.arxiv_categories,
                "keywords": topic.keywords,
                "context_keywords": topic.context_keywords,
                "negative_keywords": topic.negative_keywords,
                "weight": topic.weight,
                "enabled": topic.enabled,
                "created_at": topic.created_at,
                "updated_at": topic.updated_at,
            }
            for topic in profile.topics
        ],
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _analysis_wire(analysis: Analysis | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    payload = analysis.payload
    return {
        "id": analysis.id,
        "kind": analysis.kind,
        "status": analysis.status,
        "overview": payload.get("problem") or analysis.summary,
        "summary": analysis.summary,
        "contributions": payload.get("key_contributions", []),
        "method": "；".join(payload.get("methods", [])),
        "evidence": "；".join(payload.get("evidence", [])),
        "limitations": payload.get("limitations", analysis.weaknesses),
        "advocate": "；".join(analysis.strengths),
        "skeptic": "；".join(analysis.weaknesses),
        "input_scope": analysis.content_source,
        "budget_mode": analysis.budget_mode,
        "token_usage": {
            "available": analysis.usage_available,
            "input_tokens": analysis.input_tokens,
            "cached_input_tokens": analysis.cached_input_tokens,
            "output_tokens": analysis.output_tokens,
            "reasoning_tokens": analysis.reasoning_tokens,
            "total_tokens": analysis.total_tokens,
        },
        "relevance_score": analysis.relevance_score,
        "novelty_score": analysis.novelty_score,
        "method_score": analysis.rigor_score,
        "evidence_score": analysis.rigor_score,
        "potential_score": analysis.impact_score,
        "verdict": analysis.verdict,
        "strengths": analysis.strengths,
        "weaknesses": analysis.weaknesses,
        "tags": analysis.tags,
        "payload": payload,
        "error": analysis.error,
        "created_at": analysis.created_at,
    }


def _reading_wire(reading: PaperReadingState | None) -> dict[str, Any]:
    return {
        "state": reading.state if reading else "unread",
        "priority": reading.priority if reading else 3,
        "notes": reading.notes if reading else "",
        "tags": reading.tags if reading else [],
        "created_at": reading.created_at if reading else None,
        "updated_at": reading.updated_at if reading else None,
    }


def _project_membership_wire(link: ProjectPaper, *, include_paper: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": link.id,
        "project_id": link.project_id,
        "paper_id": link.paper_id,
        "project_name": link.project.name if link.project else None,
        "project_color": link.project.color if link.project else None,
        "reading_state": link.reading_state,
        "priority": link.priority,
        "notes": link.notes,
        "tags": link.tags,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }
    if include_paper:
        result["paper"] = {
            "id": link.paper.id,
            "arxiv_id": link.paper.arxiv_id,
            "title": link.paper.title,
            "authors": link.paper.authors,
            "primary_category": link.paper.primary_category,
            "published_at": link.paper.published_at,
        }
    return result


def _project_wire(project: ResearchProject, *, detail: bool = False) -> dict[str, Any]:
    state_counts = Counter(link.reading_state for link in project.paper_links)
    result: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "kind": project.kind,
        "is_system": project.kind == FAVORITES_LIBRARY_KIND,
        "status": project.status,
        "color": project.color,
        "goal": project.goal,
        "due_date": project.due_date,
        "paper_count": len(project.paper_links),
        "reading_counts": dict(state_counts),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if detail:
        result["papers"] = [
            _project_membership_wire(link, include_paper=True) for link in project.paper_links
        ]
    return result


def _ensure_favorites_library(db: Session, profile_id: int) -> ResearchProject:
    library = db.scalar(
        select(ResearchProject).where(
            ResearchProject.profile_id == profile_id,
            ResearchProject.kind == FAVORITES_LIBRARY_KIND,
        )
    )
    if library is not None:
        return library
    library = db.scalar(
        select(ResearchProject).where(
            ResearchProject.profile_id == profile_id,
            ResearchProject.name == FAVORITES_LIBRARY_NAME,
        )
    )
    if library is None:
        library = ResearchProject(
            profile_id=profile_id,
            name=FAVORITES_LIBRARY_NAME,
            description="收藏的论文会自动归入这里。",
            kind=FAVORITES_LIBRARY_KIND,
            status="active",
            color=FAVORITES_LIBRARY_COLOR,
        )
        db.add(library)
    else:
        library.kind = FAVORITES_LIBRARY_KIND
    db.flush()
    existing_paper_ids = {link.paper_id for link in library.paper_links}
    saved_paper_ids = set(
        db.scalars(
            select(Feedback.paper_id).where(
                Feedback.profile_id == profile_id,
                Feedback.action == "save",
            )
        ).all()
    )
    for paper_id in saved_paper_ids - existing_paper_ids:
        db.add(
            ProjectPaper(
                project_id=library.id,
                paper_id=paper_id,
                reading_state="queued",
            )
        )
    for paper_id in existing_paper_ids - saved_paper_ids:
        _set_saved_feedback(db, profile_id, paper_id, True)
    return library


def _set_saved_feedback(db: Session, profile_id: int, paper_id: int, saved: bool) -> Feedback:
    feedback = db.scalar(
        select(Feedback).where(
            Feedback.paper_id == paper_id,
            Feedback.profile_id == profile_id,
        )
    )
    if feedback is None:
        feedback = Feedback(paper_id=paper_id, profile_id=profile_id)
        db.add(feedback)
    if saved:
        feedback.action = "save"
    elif feedback.action == "save":
        feedback.action = "neutral"
    return feedback


def _sync_favorites_membership(
    db: Session,
    profile_id: int,
    paper_id: int,
    *,
    saved: bool,
) -> ProjectPaper | None:
    library = _ensure_favorites_library(db, profile_id)
    membership = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == library.id,
            ProjectPaper.paper_id == paper_id,
        )
    )
    if saved and membership is None:
        reading = db.scalar(
            select(PaperReadingState).where(
                PaperReadingState.paper_id == paper_id,
                PaperReadingState.profile_id == profile_id,
            )
        )
        membership = ProjectPaper(
            project_id=library.id,
            paper_id=paper_id,
            reading_state=(
                reading.state if reading is not None and reading.state != "unread" else "queued"
            ),
            priority=reading.priority if reading is not None else 3,
            tags_json=reading.tags if reading is not None else [],
        )
        db.add(membership)
    elif not saved and membership is not None:
        db.delete(membership)
        membership = None
    return membership


def _paper_wire(
    paper: Paper,
    profile_id: int | None = None,
    *,
    detail: bool = False,
    topics: list[Topic] | None = None,
    taxonomy_fingerprint: str | None = None,
) -> dict[str, Any]:
    match = next(
        (item for item in paper.matches if profile_id is None or item.profile_id == profile_id),
        None,
    )
    feedback = next(
        (
            item
            for item in paper.feedback_items
            if profile_id is None or item.profile_id == profile_id
        ),
        None,
    )
    reading = next(
        (
            item
            for item in paper.reading_states
            if profile_id is None or item.profile_id == profile_id
        ),
        None,
    )
    project_links = [
        item
        for item in paper.project_links
        if profile_id is None or item.project.profile_id == profile_id
    ]
    reading_data = _reading_wire(reading)
    analysis = _latest_analysis(paper, "deep", profile_id=profile_id) or _latest_analysis(
        paper, profile_id=profile_id
    )
    score = (
        match.recommendation_score
        if match and match.recommendation_score is not None
        else (match.relevance_score if match else 0)
    )
    relevance = (
        analysis.relevance_score
        if analysis and analysis.relevance_score is not None
        else (match.relevance_score if match else match.lexical_score if match else 0)
    )
    novelty = analysis.novelty_score if analysis else None
    rigor = analysis.rigor_score if analysis else None
    priority = "high" if score >= 80 else "medium" if score >= 60 else "low"
    semantic_candidates: list[dict[str, Any]] = []
    if match is not None:
        semantic_candidates = [
            {"name": name, "score": round(float(value), 4)}
            for name, value in sorted(match.semantic_topics.items(), key=lambda item: -item[1])
        ]
    # Formal directions come only from a completed AI screening under the current
    # controlled taxonomy. Semantic and lexical signals remain visible as candidates.
    if topics is not None:
        active_topics = [topic for topic in topics if topic.enabled]
        matched_topics: list[str] = []
        direction_names = {_direction_key(topic.name): topic.name for topic in active_topics}
        screening = _latest_analysis(paper, "screening", profile_id=profile_id)
        payload = dict(screening.payload_json or {}) if screening is not None else {}
        if (
            screening is not None
            and screening.status == "completed"
            and taxonomy_fingerprint is not None
            and has_current_direction_attribution(payload, taxonomy_fingerprint)
        ):
            verified = canonical_requested_direction_names(
                payload.get("matched_topics", []),
                active_topics,
                relevant=payload.get("relevant") is True,
            )
            matched_topics = _canonical_directions(verified, direction_names)
    else:
        matched_topics = _clean_string_list(match.matched_topics if match else [], limit=20)
    matched_keywords = _clean_string_list(match.matched_keywords if match else [], limit=30)
    reasons = list(dict.fromkeys([*matched_topics, *matched_keywords]))
    data: dict[str, Any] = {
        "id": paper.id,
        "arxiv_id": paper.arxiv_id,
        "version": f"v{paper.latest_version}",
        "latest_version": paper.latest_version,
        "title": paper.title,
        "authors": paper.authors,
        "affiliations": paper.affiliations,
        "notable_organizations": paper_organizations(paper),
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "abstract": paper.abstract,
        "submitted_at": paper.published_at or paper.first_seen_at,
        "published_at": paper.published_at,
        "updated_at": paper.arxiv_updated_at,
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url,
        "html_url": paper.html_url,
        "relevance_score": round(float(relevance or 0), 1),
        "semantic_score": round(float(match.semantic_score), 4)
        if match is not None and match.semantic_score is not None
        else None,
        "semantic_candidate_topics": semantic_candidates,
        "quality_score": rigor,
        "novelty_score": novelty,
        "recommendation_score": round(float(score or 0), 1),
        "priority": priority,
        "match_reasons": reasons,
        "matched_topics": matched_topics,
        "matched_keywords": matched_keywords,
        "analysis_tags": _clean_string_list(analysis.tags if analysis else [], limit=20),
        "summary": analysis.summary if analysis else None,
        "why_recommended": match.reason if match else None,
        "feedback": {
            "action": feedback.action,
            "note": feedback.note,
            "rating": feedback.rating,
            "reasons": [reason.code for reason in feedback.reasons],
        }
        if feedback
        else None,
        "feedback_reasons": [reason.code for reason in feedback.reasons] if feedback else [],
        "reading": reading_data,
        "reading_state": reading_data["state"],
        "reading_priority": reading_data["priority"],
        "notes": reading_data["notes"],
        "tags": reading_data["tags"],
        "project_memberships": [_project_membership_wire(item) for item in project_links],
        "projects": [_project_membership_wire(item) for item in project_links],
        "analysis": _analysis_wire(analysis),
    }
    if detail:
        data["comment"] = paper.comment
        data["journal_ref"] = paper.journal_ref
        data["doi"] = paper.doi
        data["versions"] = [
            {
                "id": version.id,
                "version": version.version,
                "arxiv_version_id": version.arxiv_version_id,
                "title": version.title,
                "abstract": version.abstract,
                "authors": version.authors,
                "affiliations": version.affiliations,
                "categories": version.categories,
                "abs_url": version.abs_url,
                "html_url": version.html_url,
                "pdf_url": version.pdf_url,
                "published_at": version.published_at,
                "updated_at": version.arxiv_updated_at,
            }
            for version in paper.versions
        ]
        data["analyses"] = [
            _analysis_wire(item)
            for item in sorted(paper.analyses, key=lambda x: x.created_at, reverse=True)
        ]
    return data


def _job_wire(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    data = {
        "id": job.id,
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "result": job.result,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
    for key in (
        "screening_queue_total",
        "screening_completed",
        "screening_failed",
        "screening_current_batch",
        "screening_total_batches",
        "report_incomplete",
    ):
        if key in job.result:
            data[key] = job.result[key]
    return data


def _settings_wire(db: Session) -> dict[str, Any]:
    values = _setting_map(db)
    config = get_config()
    saved_key = values.get("api_key")
    base_url = values.get("base_url") or config.base_url
    model = values.get("model") or config.model
    analysis_budget_mode = str(values.get("analysis_budget_mode") or "balanced")
    bind = db.get_bind()
    database_path: str | None = None
    database_size_bytes: int | None = None
    if bind.dialect.name == "sqlite" and bind.url.database not in (None, ":memory:"):
        try:
            path = Path(str(bind.url.database)).resolve()
            database_path = str(path)
            database_size_bytes = path.stat().st_size if path.exists() else 0
        except (OSError, ValueError):
            pass
    prompt_settings = resolve_prompt_settings(values)
    screen_batch_size = int(values.get("screen_batch_size", values.get("screen_top_n", 30)) or 0)
    smtp_password = str(
        xivpulse_env("SMTP_PASSWORD") or values.get("smtp_password") or ""
    )
    return {
        "provider": values.get("provider_name") or config.provider_name,
        "provider_name": values.get("provider_name") or config.provider_name,
        "base_url": base_url,
        "model": model,
        "api_key_configured": bool(saved_key or config.api_key),
        "source": "database"
        if any(key in values for key in ("base_url", "model", "api_key"))
        else config.config_source,
        "config_source": config.config_source,
        "key_source": "database" if saved_key else config.key_source,
        "refresh_time": values.get("schedule_time", "13:30"),
        "schedule_time": values.get("schedule_time", "13:30"),
        "timezone": values.get("timezone", "Asia/Shanghai"),
        "max_candidates": values.get("max_results", 1000),
        "max_results": values.get("max_results", 1000),
        "screen_top_n": screen_batch_size,
        "screen_batch_size": screen_batch_size,
        "semantic_recall_mode": recall_mode(values.get("semantic_recall_mode")),
        "semantic_recall_presets": {
            mode: {
                "semantic_threshold": RECALL_THRESHOLDS[mode],
                "lexical_fallback": LEXICAL_FLOORS[mode],
            }
            for mode in ("high", "balanced", "precise")
        },
        "embedding": {
            **embedding_status(),
            "backfill": values.get("embedding_backfill", {"status": "pending"}),
        },
        "deep_analysis_limit": values.get("deep_analysis_top_n", 5),
        "deep_analysis_top_n": values.get("deep_analysis_top_n", 5),
        "deep_relevance_threshold": values.get("deep_relevance_threshold", 90),
        "retention_days": values.get("retention_days", 365),
        "analysis_budget_mode": analysis_budget_mode,
        "daily_ai_synthesis_enabled": bool(
            values.get("daily_ai_synthesis_enabled", True)
        ),
        **prompt_settings,
        "automatic_cleanup_enabled": False,
        "database_path": database_path,
        "database_size_bytes": database_size_bytes,
        "stored_paper_count": db.scalar(select(func.count()).select_from(Paper)) or 0,
        "stored_analysis_count": db.scalar(select(func.count()).select_from(Analysis)) or 0,
        "stored_report_count": (
            db.scalar(select(func.count()).select_from(DailyResearchReport)) or 0
        ),
        "token_usage": _token_usage_summary(db),
        "api_mode": config.api_mode,
        "mock_mode": not bool(saved_key or config.api_key),
        "email_enabled": _email_enabled(values),
        "smtp_host": str(xivpulse_env("SMTP_HOST") or values.get("smtp_host") or ""),
        "smtp_port": int(xivpulse_env("SMTP_PORT") or values.get("smtp_port") or 587),
        "smtp_security": str(
            xivpulse_env("SMTP_SECURITY")
            or values.get("smtp_security")
            or "starttls"
        ),
        "smtp_username": str(
            xivpulse_env("SMTP_USERNAME") or values.get("smtp_username") or ""
        ),
        "smtp_password_configured": bool(smtp_password),
        "smtp_from": str(xivpulse_env("SMTP_FROM") or values.get("smtp_from") or ""),
        "email_recipients": _clean_string_list(
            [
                item.strip()
                for item in str(xivpulse_env("EMAIL_RECIPIENTS", "")).split(",")
                if item.strip()
            ]
            or values.get("email_recipients")
        ),
        "public_base_url": str(
            xivpulse_env("PUBLIC_BASE_URL")
            or values.get("public_base_url")
            or ""
        ),
    }


def _token_usage_summary(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(
            Analysis.kind,
            func.count(Analysis.id),
            func.sum(Analysis.input_tokens),
            func.sum(Analysis.cached_input_tokens),
            func.sum(Analysis.output_tokens),
            func.sum(Analysis.reasoning_tokens),
            func.sum(Analysis.total_tokens),
            func.sum(func.cast(Analysis.usage_available, Integer)),
        ).group_by(Analysis.kind)
    ).all()
    by_kind: dict[str, dict[str, Any]] = {}
    total_records = 0
    available_records = 0
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    for row in rows:
        kind = str(row[0])
        record_count = int(row[1] or 0)
        usage_count = int(row[7] or 0)
        item = {
            "record_count": record_count,
            "usage_available_count": usage_count,
            "usage_missing_count": record_count - usage_count,
            "input_tokens": int(row[2] or 0),
            "cached_input_tokens": int(row[3] or 0),
            "output_tokens": int(row[4] or 0),
            "reasoning_tokens": int(row[5] or 0),
            "total_tokens": int(row[6] or 0),
        }
        by_kind[kind] = item
        total_records += record_count
        available_records += usage_count
        for key in totals:
            totals[key] += item[key]
    report_row = db.execute(
        select(
            func.count(ReportSynthesisRun.id),
            func.sum(ReportSynthesisRun.input_tokens),
            func.sum(ReportSynthesisRun.cached_input_tokens),
            func.sum(ReportSynthesisRun.output_tokens),
            func.sum(ReportSynthesisRun.reasoning_tokens),
            func.sum(ReportSynthesisRun.total_tokens),
            func.sum(func.cast(ReportSynthesisRun.usage_available, Integer)),
        )
    ).one()
    report_record_count = int(report_row[0] or 0)
    if report_record_count:
        report_usage_count = int(report_row[6] or 0)
        report_item = {
            "record_count": report_record_count,
            "usage_available_count": report_usage_count,
            "usage_missing_count": report_record_count - report_usage_count,
            "input_tokens": int(report_row[1] or 0),
            "cached_input_tokens": int(report_row[2] or 0),
            "output_tokens": int(report_row[3] or 0),
            "reasoning_tokens": int(report_row[4] or 0),
            "total_tokens": int(report_row[5] or 0),
        }
        by_kind["report_synthesis"] = report_item
        total_records += report_record_count
        available_records += report_usage_count
        for key in totals:
            totals[key] += report_item[key]

    latest_analysis = db.scalar(
        select(Analysis)
        .where(Analysis.usage_available.is_(True))
        .order_by(desc(Analysis.created_at), desc(Analysis.id))
    )
    latest_report = db.scalar(
        select(ReportSynthesisRun)
        .where(ReportSynthesisRun.usage_available.is_(True))
        .order_by(desc(ReportSynthesisRun.created_at), desc(ReportSynthesisRun.id))
    )
    latest: Analysis | ReportSynthesisRun | None = latest_analysis
    if latest_report is not None and (
        latest is None
        or _as_utc(latest_report.created_at) >= _as_utc(latest.created_at)
    ):
        latest = latest_report
    latest_kind = (
        "report_synthesis" if isinstance(latest, ReportSynthesisRun) else latest.kind
        if latest is not None
        else None
    )
    return {
        **totals,
        "record_count": total_records,
        "usage_available_count": available_records,
        "usage_missing_count": total_records - available_records,
        "by_kind": by_kind,
        "latest": (
            {
                "analysis_id": latest.id,
                "record_id": latest.id,
                "kind": latest_kind,
                "budget_mode": latest.budget_mode,
                "input_tokens": latest.input_tokens,
                "cached_input_tokens": latest.cached_input_tokens,
                "output_tokens": latest.output_tokens,
                "reasoning_tokens": latest.reasoning_tokens,
                "total_tokens": latest.total_tokens,
                "created_at": latest.created_at,
            }
            if latest is not None
            else None
        ),
    }


def _query_papers(db: Session):
    return select(Paper).options(
        selectinload(Paper.matches),
        selectinload(Paper.analyses),
        selectinload(Paper.feedback_items).selectinload(Feedback.reasons),
        selectinload(Paper.reading_states),
        selectinload(Paper.project_links).selectinload(ProjectPaper.project),
        selectinload(Paper.versions),
    )


def _latest_complete_submission_date(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=1)


async def _sync_stage() -> dict[str, Any]:
    with SessionLocal() as db:
        profile = _active_profile(db)
        settings = _setting_map(db)
        topics = list(profile.topics)
        max_results = int(settings.get("max_results", 1000))
        screen_batch_size = int(
            settings.get("screen_batch_size", settings.get("screen_top_n", 30)) or 0
        )
        selected_recall_mode = recall_mode(settings.get("semantic_recall_mode"))
    target_date = _latest_complete_submission_date()
    client = ArxivClient()
    records = await client.fetch_daily(
        topics,
        target_date=target_date,
        max_results=max_results,
    )
    records = records[:max_results]
    enrichment_candidates: list[tuple[float, str]] = []
    queued_count = 0
    with session_scope() as db:
        result = upsert_papers(db, records)
        profile = _active_profile(db)
        author_watchlist = _clean_string_list(_setting_map(db).get("author_watchlist", []))
        watched_paper_ids: set[int] = set()
        for paper in result.papers:
            match = db.scalar(
                select(PaperMatch).where(
                    PaperMatch.paper_id == paper.id, PaperMatch.profile_id == profile.id
                )
            )
            if match is None:
                match = PaperMatch(paper_id=paper.id, profile_id=profile.id)
                db.add(match)
            paper_text = f"{paper.title}\n{paper.abstract}"
            topic_hits: list[str] = []
            keyword_hits: list[str] = []
            excluded: list[str] = []
            watched_categories = {
                category
                for topic in profile.topics
                if topic.enabled
                for category in topic.arxiv_categories
            }
            category_hits = set(paper.categories) & watched_categories
            weighted = 25.0 if "cs.RO" in paper.categories else (8.0 if category_hits else 0.0)
            watched_authors = [
                watched
                for watched in author_watchlist
                if any(watched.casefold() in author.casefold() for author in paper.authors)
            ]
            if watched_authors:
                weighted += 35.0
                watched_paper_ids.add(paper.id)
            for topic in profile.topics:
                if not topic.enabled:
                    continue
                evidence = direction_evidence(paper_text, topic)
                title_hits = [
                    keyword for keyword in topic.keywords if contains_term(paper.title, keyword)
                ]
                abstract_hits = [
                    keyword
                    for keyword in topic.keywords
                    if keyword not in title_hits and contains_term(paper.abstract, keyword)
                ]
                hits = [*title_hits, *abstract_hits]
                negatives = list(evidence.exclusions)
                if evidence.matched:
                    topic_hits.append(topic.name)
                    keyword_hits.extend(hits)
                    weighted += 12 * topic.weight
                    weighted += min(
                        58,
                        (30 * len(title_hits) + 18 * len(abstract_hits)) * topic.weight,
                    )
                if hits:
                    excluded.extend(negatives)
            match.matched_topics_json = list(dict.fromkeys(topic_hits))
            match.matched_keywords_json = list(dict.fromkeys(keyword_hits))
            match.excluded_keywords_json = list(dict.fromkeys(excluded))
            match.lexical_score = max(0, min(100, weighted - len(excluded) * 30))
            if match.screened_at is None:
                match.relevance_score = match.lexical_score
            match.recommendation_score = match.relevance_score
            match.reason = (
                "、".join(match.matched_topics_json[:3])
                or (f"命中关注作者：{'、'.join(watched_authors[:3])}" if watched_authors else "")
                or f"关注分类：{paper.primary_category or '未标注'}，等待摘要相关度分析"
            )
        db.flush()
        semantic_error: str | None = None
        try:
            semantic_results = semantic_match_papers(
                db, list(result.papers), list(profile.topics)
            )
        except SemanticEmbeddingError as exc:
            # A first-run model download can be unavailable on an offline machine.
            # Keep the daily pipeline usable through the lexical/author fallback and
            # surface the degraded recall explicitly in the job result.
            semantic_results = {}
            semantic_error = str(exc)
        for paper in result.papers:
            match = db.scalar(
                select(PaperMatch).where(
                    PaperMatch.paper_id == paper.id, PaperMatch.profile_id == profile.id
                )
            )
            if match is None:
                continue
            semantic = semantic_results.get(paper.id)
            match.semantic_score = semantic.score if semantic is not None else None
            match.semantic_topics_json = semantic.topic_scores if semantic is not None else {}
            match.semantic_model = SEMANTIC_MODEL_ID if semantic is not None else None
            candidate = is_semantic_candidate(
                semantic_score=match.semantic_score,
                lexical_score=match.lexical_score,
                mode=selected_recall_mode,
                watched_author=paper.id in watched_paper_ids,
            )
            if match.screened_at is None and candidate:
                queue_item = db.scalar(
                    select(ScreeningQueueItem).where(
                        ScreeningQueueItem.paper_id == paper.id,
                        ScreeningQueueItem.profile_id == profile.id,
                    )
                )
                if queue_item is None:
                    queue_item = ScreeningQueueItem(
                        paper_id=paper.id,
                        profile_id=profile.id,
                        report_date=target_date,
                    )
                    db.add(queue_item)
                elif queue_item.status == "failed":
                    queue_item.status = "queued"
                    queue_item.attempts = 0
                    queue_item.last_error = None
                queue_item.recall_mode = selected_recall_mode
                queue_item.semantic_score = match.semantic_score
                queue_item.semantic_topics_json = dict(match.semantic_topics_json or {})
                queued_count += 1
                if not paper.affiliations:
                    priority = max(match.lexical_score / 100.0, match.semantic_score or 0.0)
                    enrichment_candidates.append((priority, paper.arxiv_id))
        response = {
            "fetched": len(records),
            "queued": queued_count,
            "semantic_recall_mode": selected_recall_mode,
            "semantic_degraded": semantic_error is not None,
            "semantic_error": semantic_error,
            **result.as_dict(),
            "fetch_date": target_date.isoformat(),
            "query": build_profile_query(topics),
        }

    # Enrich only the papers about to enter screening, in one bounded Atom call.
    selected_ids = [
        arxiv_id for _, arxiv_id in sorted(enrichment_candidates, reverse=True)[:screen_batch_size]
    ]
    fetch_by_ids = getattr(client, "fetch_by_ids", None)
    enriched_by_id: dict[str, list[str]] = {}
    if selected_ids and fetch_by_ids is not None:
        try:
            enriched = await fetch_by_ids(selected_ids)
            if enriched:
                with session_scope() as db:
                    upsert_papers(db, enriched)
            enriched_by_id.update(
                {record.arxiv_id: record.affiliations for record in enriched if record.affiliations}
            )
        except Exception as exc:  # metadata enrichment must not abort the daily run
            LOG.warning("arXiv affiliation enrichment failed: %s", exc)
    missing_ids = [value for value in selected_ids if value not in enriched_by_id]
    fetch_semantic_affiliations = getattr(client, "fetch_semantic_scholar_affiliations", None)
    if missing_ids and fetch_semantic_affiliations is not None:
        try:
            semantic_affiliations = await fetch_semantic_affiliations(missing_ids)
            enriched_by_id.update(semantic_affiliations)
        except Exception as exc:
            LOG.warning("Semantic Scholar affiliation enrichment failed: %s", exc)
    missing_ids = [value for value in selected_ids if value not in enriched_by_id]
    fetch_html_affiliations = getattr(client, "fetch_html_affiliations", None)
    if missing_ids and fetch_html_affiliations is not None:
        try:
            html_affiliations = await fetch_html_affiliations(missing_ids[:6])
            enriched_by_id.update(html_affiliations)
        except Exception as exc:
            LOG.warning("arXiv HTML affiliation enrichment failed: %s", exc)
    if enriched_by_id:
        with session_scope() as db:
            papers = db.scalars(
                select(Paper)
                .options(selectinload(Paper.versions))
                .where(Paper.arxiv_id.in_(enriched_by_id))
            ).all()
            for paper in papers:
                affiliations = enriched_by_id[paper.arxiv_id]
                paper.affiliations = affiliations
                latest = max(
                    paper.versions,
                    key=lambda version: version.version,
                    default=None,
                )
                if latest is not None:
                    latest.affiliations = affiliations
    response["affiliations_enriched"] = len(enriched_by_id)
    return response


async def _analyze_stage(
    limit: int | None = None,
    *,
    job_id: str | None = None,
    report_date: date | None = None,
) -> dict[str, Any]:
    try:
        from .services.analyzer import OpenAICompatibleAdapter, PaperAnalyzer
    except ImportError:
        return {"analyzed": 0, "message": "分析服务尚不可用"}
    with SessionLocal() as db:
        profile = _active_profile(db)
        settings = _setting_map(db)
        limit = int(limit if limit is not None else settings.get("deep_analysis_top_n", 5))
        threshold = float(settings.get("deep_relevance_threshold", 90))
        screen_batch_size = max(
            1,
            int(settings.get("screen_batch_size", settings.get("screen_top_n", 30)) or 30),
        )
        selected_recall_mode = recall_mode(settings.get("semantic_recall_mode"))
        today = report_date or current_date_in_timezone(
            str(settings.get("timezone", "Asia/Shanghai"))
        )
        recent_matches = (
            db.scalars(
                select(PaperMatch)
                .options(
                    joinedload(PaperMatch.paper),
                    joinedload(PaperMatch.profile).selectinload(Profile.topics),
                )
                .where(
                    PaperMatch.profile_id == profile.id,
                    PaperMatch.screened_at.is_(None),
                    PaperMatch.paper.has(
                        Paper.last_seen_at >= datetime.now(UTC) - timedelta(days=7)
                    ),
                )
            )
            .unique()
            .all()
        )
        watched_authors = {
            value.casefold() for value in _clean_string_list(settings.get("author_watchlist", []))
        }
        existing_queue_keys = set(
            db.scalars(
                select(ScreeningQueueItem.paper_id).where(
                    ScreeningQueueItem.profile_id == profile.id
                )
            ).all()
        )
        for match in recent_matches:
            watched = any(
                watched_name in author.casefold()
                for author in match.paper.authors
                for watched_name in watched_authors
            )
            if match.paper_id in existing_queue_keys or not is_semantic_candidate(
                semantic_score=match.semantic_score,
                lexical_score=match.lexical_score,
                mode=selected_recall_mode,
                watched_author=watched,
            ):
                continue
            db.add(
                ScreeningQueueItem(
                    paper_id=match.paper_id,
                    profile_id=profile.id,
                    report_date=today,
                    recall_mode=selected_recall_mode,
                    semantic_score=match.semantic_score,
                    semantic_topics_json=dict(match.semantic_topics_json or {}),
                )
            )
        queue_items = db.scalars(
            select(ScreeningQueueItem).where(ScreeningQueueItem.profile_id == profile.id)
        ).all()
        for item in queue_items:
            match = db.scalar(
                select(PaperMatch).where(
                    PaperMatch.paper_id == item.paper_id,
                    PaperMatch.profile_id == profile.id,
                )
            )
            if match is not None and match.screened_at is not None:
                item.status = "completed"
                item.completed_at = item.completed_at or match.screened_at
                item.last_error = None
            elif item.status in {"failed", "processing"}:
                item.status = "queued"
                item.attempts = 0
                item.last_error = None
                item.completed_at = None
        db.commit()
    config = _configured_provider_config(settings)
    try:
        adapter = OpenAICompatibleAdapter(config=config)
    except TypeError:
        adapter = OpenAICompatibleAdapter(config)
    budget_mode = str(settings.get("analysis_budget_mode") or "balanced")
    prompt_settings = resolve_prompt_settings(settings)
    try:
        analyzer = PaperAnalyzer(
            adapter,
            budget_mode=budget_mode,
            prompt_template_id=prompt_settings["analysis_prompt_template_id"],
            screening_prompt=prompt_settings["screening_prompt"],
            deep_prompt=prompt_settings["deep_prompt"],
        )
    except TypeError:
        # Keep lightweight third-party/test analyzers compatible with the
        # original one-argument constructor.
        analyzer = PaperAnalyzer(adapter)

    screened = 0
    screening_errors: list[str] = []
    screening_failed = 0
    screening_batches = 0
    screening_report_dates: set[date] = set()
    with SessionLocal() as db:
        profile = _active_profile(db)
        screening_queue_total = int(
            db.scalar(
                select(func.count(ScreeningQueueItem.id)).where(
                    ScreeningQueueItem.profile_id == profile.id,
                    ScreeningQueueItem.status.in_(("queued", "processing", "failed")),
                )
            )
            or 0
        )
    total_batches = (
        (screening_queue_total + screen_batch_size - 1) // screen_batch_size
        if screening_queue_total
        else 0
    )

    async def screen_one(
        queue_id: int,
        match: PaperMatch,
    ) -> tuple[int, PaperMatch, Any | None, Exception | None, int]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                if hasattr(analyzer, "screen_with_metadata"):
                    generated = await analyzer.screen_with_metadata(match.paper, match.profile)
                else:
                    generated = await analyzer.screen(match.paper, match.profile)
                return queue_id, match, generated, None, attempt
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(SCREENING_RETRY_DELAYS[attempt - 1])
        return queue_id, match, None, last_error, 3

    while True:
        with session_scope() as db:
            profile = _active_profile(db)
            queue_batch = (
                db.scalars(
                    select(ScreeningQueueItem)
                    .options(
                        joinedload(ScreeningQueueItem.paper),
                        joinedload(ScreeningQueueItem.profile).selectinload(Profile.topics),
                    )
                    .where(
                        ScreeningQueueItem.profile_id == profile.id,
                        ScreeningQueueItem.status == "queued",
                    )
                    .order_by(
                        ScreeningQueueItem.report_date,
                        desc(ScreeningQueueItem.semantic_score),
                        ScreeningQueueItem.id,
                    )
                    .limit(screen_batch_size)
                )
                .unique()
                .all()
            )
            for item in queue_batch:
                item.status = "processing"
                screening_report_dates.add(item.report_date)
            batch_payload = []
            for item in queue_batch:
                match = db.scalar(
                    select(PaperMatch)
                    .options(
                        joinedload(PaperMatch.paper),
                        joinedload(PaperMatch.profile).selectinload(Profile.topics),
                    )
                    .where(
                        PaperMatch.paper_id == item.paper_id,
                        PaperMatch.profile_id == item.profile_id,
                    )
                )
                if match is None:
                    item.status = "failed"
                    item.attempts = 3
                    item.last_error = "PaperMatch no longer exists"
                    continue
                batch_payload.append((item.id, match))
        if not queue_batch:
            break
        screening_batches += 1
        batch_results: list[tuple[int, PaperMatch, Any | None, Exception | None, int]] = []
        for batch_start in range(0, len(batch_payload), 4):
            batch_results.extend(
                await asyncio.gather(
                    *(
                        screen_one(queue_id, match)
                        for queue_id, match in batch_payload[batch_start : batch_start + 4]
                    )
                )
            )
        for queue_id, match, generated, error, attempts in batch_results:
            if error is not None or generated is None:
                LOG.error("Screening failed for paper %s: %s", match.paper_id, error)
                screening_errors.append(f"{match.paper_id}: {error}")
                screening_failed += 1
                with session_scope() as db:
                    item = db.get(ScreeningQueueItem, queue_id)
                    if item is not None:
                        item.status = "failed"
                        item.attempts = attempts
                        item.last_error = str(error or "Unknown screening error")
                        item.completed_at = None
                continue
            result = getattr(generated, "data", generated)
            usage = getattr(generated, "usage", None)
            usage_fields = (
                usage.analysis_fields(budget_mode)
                if usage is not None
                else {
                    "budget_mode": budget_mode,
                    "usage_available": False,
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                    "total_tokens": None,
                }
            )
            with session_scope() as db:
                stored_match = db.get(PaperMatch, match.id)
                if stored_match is None:
                    continue
                stored_match.relevance_score = result.relevance_score
                stored_match.screened_at = utcnow()
                ai_directions = canonical_requested_direction_names(
                    result.matched_topics,
                    list(match.profile.topics),
                    relevant=result.relevant is True,
                )
                stored_match.matched_topics_json = ai_directions
                stored_match.reason = result.reason
                item = db.get(ScreeningQueueItem, queue_id)
                if item is not None:
                    item.status = "completed"
                    item.attempts = attempts
                    item.last_error = None
                    item.completed_at = stored_match.screened_at
                db.add(
                    Analysis(
                        paper_id=match.paper_id,
                        profile_id=match.profile_id,
                        kind="screening",
                        status="completed",
                        provider=config.provider_name,
                        model=config.model,
                        transport=getattr(generated, "transport", adapter.last_transport),
                        content_source="abstract",
                        summary=result.reason,
                        verdict="relevant" if result.relevant else "not_relevant",
                        relevance_score=result.relevance_score,
                        tags_json=result.tags,
                        payload_json={
                            **stamp_direction_attribution(
                                result.model_dump(), list(match.profile.topics)
                            ),
                            "_analysis_prompt": getattr(generated, "prompt_metadata", {}),
                        },
                        **usage_fields,
                    )
                )
            screened += 1
        completed_or_failed = screened + screening_failed
        progress = 35 + int(42 * completed_or_failed / max(1, screening_queue_total))
        _update_job_progress(
            job_id,
            progress=progress,
            message=(
                f"摘要初筛第 {screening_batches}/{max(total_batches, screening_batches)} 批："
                f"成功 {screened}，失败 {screening_failed}，总候选 {screening_queue_total}"
            ),
            details={
                "screening_queue_total": screening_queue_total,
                "screening_completed": screened,
                "screening_failed": screening_failed,
                "screening_current_batch": screening_batches,
                "screening_total_batches": max(total_batches, screening_batches),
                "report_incomplete": screening_failed > 0,
            },
        )

    if screening_report_dates:
        with SessionLocal() as db:
            screening_failed = int(
                db.scalar(
                    select(func.count(ScreeningQueueItem.id)).where(
                        ScreeningQueueItem.profile_id == profile.id,
                        ScreeningQueueItem.report_date.in_(screening_report_dates),
                        ScreeningQueueItem.status == "failed",
                    )
                )
                or 0
            )

    with SessionLocal() as db:
        profile = _active_profile(db)
        matches = (
            db.scalars(
                select(PaperMatch)
                .options(
                    joinedload(PaperMatch.paper),
                    joinedload(PaperMatch.profile).selectinload(Profile.topics),
                )
                .where(
                    PaperMatch.profile_id == profile.id,
                    PaperMatch.relevance_score >= threshold,
                    PaperMatch.screened_at.is_not(None),
                    PaperMatch.paper.has(
                        Paper.last_seen_at >= datetime.now(UTC) - timedelta(days=7)
                    ),
                    ~PaperMatch.paper.has(
                        Paper.analyses.any(
                            (Analysis.profile_id == profile.id)
                            & (Analysis.kind == "deep")
                            & (Analysis.status == "completed")
                        )
                    ),
                )
                .order_by(desc(PaperMatch.recommendation_score), desc(PaperMatch.lexical_score))
                .limit(max(0, limit))
            )
            .unique()
            .all()
        )
    count = 0
    errors: list[str] = []
    for match in matches:
        try:
            generated, fulltext = await analyzer.deep_analyze_with_metadata(
                match.paper, match.profile
            )
            result = generated.data
            payload = result.model_dump()
            payload["_analysis_prompt"] = getattr(generated, "prompt_metadata", {})
            with session_scope() as db:
                db.add(
                    Analysis(
                        paper_id=match.paper_id,
                        profile_id=match.profile_id,
                        kind="deep",
                        status="completed",
                        provider=config.provider_name,
                        model=config.model,
                        transport=generated.transport,
                        content_source=fulltext.source if fulltext.content else "abstract",
                        summary=payload.get("summary", ""),
                        verdict=payload.get("verdict", ""),
                        relevance_score=payload.get("relevance_score"),
                        novelty_score=payload.get("novelty_score"),
                        rigor_score=payload.get("rigor_score"),
                        impact_score=payload.get("impact_score"),
                        strengths_json=payload.get("strengths", []),
                        weaknesses_json=payload.get("weaknesses", []),
                        tags_json=payload.get("tags", []),
                        payload_json=payload,
                        **generated.usage.analysis_fields(budget_mode),
                    )
                )
                stored_match = db.scalar(select(PaperMatch).where(PaperMatch.id == match.id))
                if stored_match is not None:
                    stored_match.relevance_score = result.relevance_score
            count += 1
        except Exception as exc:  # individual papers must not abort the whole digest
            LOG.exception("Analysis failed for paper %s", match.paper_id)
            errors.append(f"{match.paper_id}: {exc}")
    with session_scope() as db:
        profile = _active_profile(db)
        RecommendationEngine().rank_session(
            db,
            profile.id,
            author_watchlist=_clean_string_list(settings.get("author_watchlist", [])),
            limit=int(settings.get("max_results", 1000)),
            since=datetime.now(UTC) - timedelta(days=7),
        )
    return {
        "analyzed": count,
        "screened": screened,
        "screening_queue_total": screening_queue_total,
        "screening_completed": screened,
        "screening_failed": screening_failed,
        "screening_batches": screening_batches,
        "screening_total_batches": max(total_batches, screening_batches),
        "report_incomplete": screening_failed > 0,
        "screening_report_dates": sorted(value.isoformat() for value in screening_report_dates),
        "eligible": len(matches),
        "threshold": threshold,
        "screening_errors": screening_errors[:10],
        "analysis_errors": errors[:10],
    }


def _create_digest(digest_date: date | None = None) -> dict[str, Any]:
    with session_scope() as db:
        profile = _active_profile(db)
        matches = db.scalars(
            select(PaperMatch)
            .where(PaperMatch.profile_id == profile.id)
            .order_by(desc(PaperMatch.recommendation_score))
            .limit(20)
        ).all()
        timezone_name = str(_setting_map(db).get("timezone", "Asia/Shanghai"))
        today = digest_date or current_date_in_timezone(timezone_name)
        digest = db.scalar(
            select(Digest).where(Digest.profile_id == profile.id, Digest.digest_date == today)
        )
        if digest is None:
            digest = Digest(
                profile_id=profile.id,
                digest_date=today,
                title=f"{today.isoformat()} arXiv 每日精选",
            )
            db.add(digest)
        digest.paper_ids_json = [item.paper_id for item in matches]
        digest.summary = f"今日筛选出 {len(matches)} 篇与你的研究画像相关的论文。"
        digest.stats_json = {"recommended": len(matches)}
        digest.status = "ready"
        db.flush()
        return {"digest_id": digest.id, "recommended": len(matches)}


def _daily_report_snapshot_wire(snapshot: DailyResearchReport) -> dict[str, Any]:
    payload = snapshot.payload
    return {
        **payload,
        "persisted": True,
        "snapshot_id": snapshot.id,
        "generated_at": _as_utc(snapshot.created_at),
        "updated_at": _as_utc(snapshot.updated_at),
    }


def _persist_daily_research_report(
    db: Session,
    profile_id: int,
    *,
    report_date: date | None = None,
    timezone_name: str,
    max_papers: int = 10,
    refresh: bool = False,
) -> DailyResearchReport:
    """Return the saved report for a day, creating or explicitly refreshing it."""

    day = report_date or current_date_in_timezone(timezone_name)
    snapshot = db.scalar(
        select(DailyResearchReport).where(
            DailyResearchReport.profile_id == profile_id,
            DailyResearchReport.report_date == day,
        )
    )
    if snapshot is not None and not refresh:
        return snapshot

    report = build_daily_research_report(
        db,
        profile_id,
        report_date=day,
        timezone_name=timezone_name,
        highlight_limit=min(max_papers, 10),
        reading_queue_limit=max_papers,
        custom_method_terms=_clean_custom_methods(_setting_map(db).get("custom_method_terms", [])),
    )
    encoded = jsonable_encoder(report)
    if not isinstance(encoded, dict):
        raise TypeError("Daily research report must serialize to a JSON object")
    previous_synthesis = snapshot.payload.get("ai_synthesis") if snapshot is not None else None
    if isinstance(previous_synthesis, dict):
        preserved_synthesis = dict(previous_synthesis)
        preserved_synthesis["stale"] = (
            str(preserved_synthesis.get("source_hash") or "")
            != report_source_hash(encoded)
        )
        encoded["ai_synthesis"] = preserved_synthesis
    if snapshot is None:
        snapshot = DailyResearchReport(
            profile_id=profile_id,
            report_date=day,
            payload_json=encoded,
        )
        db.add(snapshot)
    else:
        snapshot.payload_json = encoded
        snapshot.updated_at = utcnow()
    db.flush()
    return snapshot


def _create_daily_research_report(
    *,
    report_date: date | None = None,
    screening_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh today's deterministic report after the daily analysis pipeline."""

    with session_scope() as db:
        profile = _active_profile(db)
        timezone_name = str(_setting_map(db).get("timezone", "Asia/Shanghai"))
        selected_date = report_date or current_date_in_timezone(timezone_name)
        snapshot = _persist_daily_research_report(
            db,
            profile.id,
            report_date=selected_date,
            timezone_name=timezone_name,
            refresh=True,
        )
        if screening_status:
            payload = snapshot.payload
            coverage = dict(payload.get("coverage") or {})
            coverage.update(
                {
                    "screening_queue_total": int(
                        screening_status.get("screening_queue_total", 0) or 0
                    ),
                    "screening_completed": int(screening_status.get("screening_completed", 0) or 0),
                    "screening_failed": int(screening_status.get("screening_failed", 0) or 0),
                }
            )
            payload["coverage"] = coverage
            payload["incomplete"] = bool(screening_status.get("report_incomplete", False))
            payload["screening_status"] = {
                "total": coverage["screening_queue_total"],
                "completed": coverage["screening_completed"],
                "failed": coverage["screening_failed"],
                "batches": int(screening_status.get("screening_batches", 0) or 0),
            }
            snapshot.payload_json = payload
            snapshot.updated_at = utcnow()
        return {
            "report_snapshot_id": snapshot.id,
            "report_date": snapshot.report_date.isoformat(),
        }


def _synthesis_state(
    previous: dict[str, Any] | None,
    *,
    status_name: str,
    source_hash: str,
    error: str | None = None,
) -> dict[str, Any]:
    state = dict(previous or {})
    previous_hash = str(state.get("source_hash") or "")
    state.update(
        {
            "status": status_name,
            "source_hash": source_hash,
            "stale": bool(previous_hash and previous_hash != source_hash),
        }
    )
    if error:
        state["last_error"] = error
    else:
        state.pop("last_error", None)
    return state


async def _synthesize_daily_research_report(
    report_date: date,
    *,
    force: bool = False,
    respect_enabled: bool = True,
) -> dict[str, Any]:
    """Generate at most one saved synthesis for the current deterministic inputs."""

    with session_scope() as db:
        profile = _active_profile(db)
        settings = _setting_map(db)
        snapshot = db.scalar(
            select(DailyResearchReport).where(
                DailyResearchReport.profile_id == profile.id,
                DailyResearchReport.report_date == report_date,
            )
        )
        if snapshot is None:
            raise ValueError(f"没有找到 {report_date.isoformat()} 的研究日报")
        report = snapshot.payload
        previous = report.get("ai_synthesis")
        previous = dict(previous) if isinstance(previous, dict) else None
        source_hash = report_source_hash(report)
        enabled = bool(settings.get("daily_ai_synthesis_enabled", True))
        if respect_enabled and not enabled:
            if previous is None:
                report["ai_synthesis"] = _synthesis_state(
                    None, status_name="disabled", source_hash=source_hash
                )
                snapshot.payload_json = report
            return {"report_synthesis_status": "disabled", "report_date": report_date.isoformat()}
        if not synthesis_has_enough_data(report):
            report["ai_synthesis"] = _synthesis_state(
                previous, status_name="insufficient_data", source_hash=source_hash
            )
            snapshot.payload_json = report
            snapshot.updated_at = utcnow()
            return {
                "report_synthesis_status": "insufficient_data",
                "report_date": report_date.isoformat(),
            }
        if (
            not force
            and previous is not None
            and previous.get("status") == "ready"
            and previous.get("source_hash") == source_hash
        ):
            return {"report_synthesis_status": "ready", "report_date": report_date.isoformat()}
        config = _configured_provider_config(settings)
        if config.mock_mode:
            report["ai_synthesis"] = _synthesis_state(
                previous, status_name="not_configured", source_hash=source_hash
            )
            snapshot.payload_json = report
            snapshot.updated_at = utcnow()
            return {
                "report_synthesis_status": "not_configured",
                "report_date": report_date.isoformat(),
            }
        budget_mode = str(settings.get("analysis_budget_mode") or "balanced")
        run = ReportSynthesisRun(
            profile_id=profile.id,
            report_id=snapshot.id,
            report_date=report_date,
            status="running",
            source_hash=source_hash,
            provider=config.provider_name,
            model=config.model,
            transport=config.api_mode,
            budget_mode=budget_mode,
        )
        db.add(run)
        db.flush()
        run_id = run.id

    from .services.analyzer import OpenAICompatibleAdapter, ProviderError

    try:
        adapter = OpenAICompatibleAdapter(config)
        generated = await generate_daily_report_synthesis(
            adapter,
            report,
            budget_mode=budget_mode,
        )
        usage = generated.usage
        generated_at = utcnow()
        synthesis = {
            "status": "ready",
            **generated.data.model_dump(),
            "generated_at": generated_at.isoformat(),
            "provider": generated.provider,
            "model": generated.model,
            "transport": generated.transport,
            "budget_mode": budget_mode,
            "source_hash": source_hash,
            "stale": False,
            "usage": {
                "available": usage.available,
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
            },
        }
        with session_scope() as db:
            run = db.get(ReportSynthesisRun, run_id)
            snapshot = db.get(DailyResearchReport, run.report_id) if run is not None else None
            if run is None or snapshot is None:
                raise RuntimeError("日报或研判记录在生成过程中被删除")
            run.status = "ready"
            run.transport = generated.transport
            run.provider = generated.provider
            run.model = generated.model
            run.payload_json = synthesis
            for key, value in usage.analysis_fields(budget_mode).items():
                setattr(run, key, value)
            payload = snapshot.payload
            payload["ai_synthesis"] = synthesis
            snapshot.payload_json = payload
            snapshot.updated_at = generated_at
        return {"report_synthesis_status": "ready", "report_date": report_date.isoformat()}
    except Exception as exc:
        LOG.exception("Daily report AI synthesis failed for %s", report_date)
        safe_error = (
            str(exc)[:500]
            if isinstance(exc, ProviderError)
            else "AI 综合研判生成失败，请检查模型配置或稍后重试"
        )
        with session_scope() as db:
            run = db.get(ReportSynthesisRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = safe_error
                snapshot = db.get(DailyResearchReport, run.report_id)
                if snapshot is not None:
                    payload = snapshot.payload
                    payload["ai_synthesis"] = _synthesis_state(
                        previous,
                        status_name="error",
                        source_hash=source_hash,
                        error=safe_error,
                    )
                    snapshot.payload_json = payload
                    snapshot.updated_at = utcnow()
        return {
            "report_synthesis_status": "error",
            "report_synthesis_error": safe_error,
            "report_date": report_date.isoformat(),
        }


def _email_enabled(settings: dict[str, Any]) -> bool:
    environment = xivpulse_env("EMAIL_ENABLED")
    if environment is not None:
        return environment.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(settings.get("email_enabled", False))


async def _send_daily_report_email(
    report_date: date,
    *,
    delivery_kind: str = "automatic",
    require_enabled: bool = True,
) -> dict[str, Any]:
    with SessionLocal() as db:
        profile = _active_profile(db)
        settings = _setting_map(db)
        if require_enabled and not _email_enabled(settings):
            return {"email_status": "disabled", "email_sent": 0, "email_failed": 0}
        config = smtp_config(settings)
        if not config.ready:
            return {
                "email_status": "not_configured",
                "email_sent": 0,
                "email_failed": 0,
            }
        snapshot = db.scalar(
            select(DailyResearchReport).where(
                DailyResearchReport.profile_id == profile.id,
                DailyResearchReport.report_date == report_date,
            )
        )
        if snapshot is None:
            raise ValueError(f"没有找到 {report_date.isoformat()} 的研究日报")
        report = snapshot.payload
        profile_id = profile.id
        recipients = config.recipients

    sent = 0
    failed = 0
    errors: list[str] = []
    for recipient in recipients:
        with session_scope() as db:
            delivery = db.scalar(
                select(EmailDelivery).where(
                    EmailDelivery.profile_id == profile_id,
                    EmailDelivery.report_date == report_date,
                    EmailDelivery.recipient == recipient,
                    EmailDelivery.delivery_kind == delivery_kind,
                )
            )
            if delivery is not None and delivery.status == "sent":
                continue
            if delivery is None:
                delivery = EmailDelivery(
                    profile_id=profile_id,
                    report_date=report_date,
                    recipient=recipient,
                    delivery_kind=delivery_kind,
                )
                db.add(delivery)
                db.flush()
            delivery.status = "sending"
            delivery.attempts += 1
            delivery_id = delivery.id
        try:
            failure_count = int(dict(report.get("coverage") or {}).get("screening_failed", 0) or 0)
            message = build_daily_message(
                config,
                report,
                recipient,
                incomplete=bool(report.get("incomplete", False)),
                failure_count=failure_count,
            )
            await asyncio.to_thread(deliver_message, config, message)
            with session_scope() as db:
                delivery = db.get(EmailDelivery, delivery_id)
                if delivery is not None:
                    delivery.status = "sent"
                    delivery.subject = str(message["Subject"] or "")
                    delivery.last_error = None
                    delivery.sent_at = utcnow()
            sent += 1
        except Exception as exc:
            LOG.exception("Daily report email failed for %s", recipient)
            with session_scope() as db:
                delivery = db.get(EmailDelivery, delivery_id)
                if delivery is not None:
                    delivery.status = "failed"
                    delivery.last_error = str(exc)
            failed += 1
            errors.append(f"{recipient}: {exc}")
    status_name = "sent" if sent and not failed else "failed" if failed else "duplicate"
    return {
        "email_status": status_name,
        "email_sent": sent,
        "email_failed": failed,
        "email_errors": errors[:10],
    }


def _update_job_progress(
    job_id: str | None,
    *,
    progress: int,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if job_id is None:
        return
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is not None and job.status in RUNNING_STATUSES:
            job.progress = max(0, min(100, progress))
            job.message = message
            if details:
                job.result_json = {**job.result, **details}


async def _execute_pipeline(
    kind: Literal["daily", "sync", "analyze"],
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    pipeline_date: date | None = None
    if kind in ("daily", "sync"):
        _update_job_progress(job_id, progress=10, message="正在抓取并匹配 arXiv 候选")
        result.update(await _sync_stage())
        pipeline_date = date.fromisoformat(str(result["fetch_date"]))
    if kind in ("daily", "analyze"):
        _update_job_progress(job_id, progress=35, message="正在进行 AI 摘要初筛与全文深读")
        result.update(await _analyze_stage(job_id=job_id, report_date=pipeline_date))
    if kind == "daily":
        if pipeline_date is None:
            raise RuntimeError("Daily pipeline is missing its fetch date")
        _update_job_progress(job_id, progress=88, message="正在重排推荐并保存当日研究日报")
        result.update(_create_digest(pipeline_date))
        result.update(
            _create_daily_research_report(report_date=pipeline_date, screening_status=result)
        )
        current_report_date = date.fromisoformat(str(result["report_date"]))
        refreshed_report_dates: list[str] = []
        for value in result.get("screening_report_dates", []):
            selected_date = date.fromisoformat(str(value))
            if selected_date == current_report_date:
                continue
            with SessionLocal() as db:
                profile = _active_profile(db)
                status_rows = dict(
                    db.execute(
                        select(
                            ScreeningQueueItem.status,
                            func.count(ScreeningQueueItem.id),
                        )
                        .where(
                            ScreeningQueueItem.profile_id == profile.id,
                            ScreeningQueueItem.report_date == selected_date,
                        )
                        .group_by(ScreeningQueueItem.status)
                    ).all()
                )
            total = sum(int(count) for count in status_rows.values())
            failed = int(status_rows.get("failed", 0))
            _create_daily_research_report(
                report_date=selected_date,
                screening_status={
                    "screening_queue_total": total,
                    "screening_completed": int(status_rows.get("completed", 0)),
                    "screening_failed": failed,
                    "screening_batches": 0,
                    "report_incomplete": failed > 0,
                },
            )
            refreshed_report_dates.append(selected_date.isoformat())
        result["refreshed_report_dates"] = refreshed_report_dates
        _update_job_progress(job_id, progress=94, message="正在生成并保存 AI 综合研判")
        result.update(
            await _synthesize_daily_research_report(
                current_report_date,
                force=False,
                respect_enabled=True,
            )
        )
        result.update(
            await _send_daily_report_email(
                current_report_date,
                delivery_kind="automatic",
            )
        )
    return result


async def run_daily_pipeline(trigger: str = "manual") -> dict[str, Any]:
    """Run once in the caller process, used by Windows Task Scheduler."""
    global _active_job_id
    initialize_application()
    with _job_lock:
        if _active_job_id is not None:
            return {"accepted": False, "success": False, "message": "已有任务正在运行"}
        lock_token = f"direct:{uuid4()}"
        if not _acquire_pipeline_lock(lock_token):
            return {"accepted": False, "success": False, "message": "已有任务正在运行"}
        try:
            with session_scope() as db:
                existing = db.scalar(
                    select(Job)
                    .where(Job.status.in_(RUNNING_STATUSES))
                    .order_by(desc(Job.created_at))
                )
                if existing:
                    heartbeat = _as_utc(existing.started_at or existing.created_at)
                    if not heartbeat or heartbeat >= utcnow() - timedelta(hours=2):
                        _release_pipeline_lock(lock_token)
                        return {
                            "accepted": False,
                            "success": False,
                            "message": "已有任务正在运行",
                        }
                    existing.status = "failed"
                    existing.error = "应用退出后遗留的任务已自动关闭"
                    existing.completed_at = utcnow()
                job = Job(
                    kind="daily",
                    status="running",
                    message=f"计划任务执行中（{trigger}）",
                    progress=5,
                    started_at=utcnow(),
                )
                db.add(job)
                db.flush()
                job_id = job.id
        except Exception:
            _release_pipeline_lock(lock_token)
            raise
        if "job_id" not in locals():
            _release_pipeline_lock(lock_token)
            return {"accepted": False, "success": False, "message": "无法创建任务"}
        _active_job_id = job_id
    try:
        result = await _execute_pipeline("daily", job_id=job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "completed"
                job.progress = 100
                job.result_json = result
                job.message = "计划任务已完成"
                job.completed_at = utcnow()
        return {"accepted": True, "success": True, **result}
    except Exception as exc:
        LOG.exception("Direct daily pipeline failed")
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.message = "计划任务失败"
                job.completed_at = utcnow()
        return {"accepted": True, "success": False, "error": str(exc)}
    finally:
        with _job_lock:
            if _active_job_id == job_id:
                _active_job_id = None
        _release_pipeline_lock(lock_token)


async def _run_job(job_id: str, kind: Literal["daily", "sync", "analyze"]) -> None:
    global _active_job_id
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = utcnow()
            job.message = "任务执行中"
            job.progress = 5
        result = await _execute_pipeline(kind, job_id=job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "completed"
                job.progress = 100
                job.result_json = result
                job.message = "任务已完成"
                job.completed_at = utcnow()
    except Exception as exc:
        LOG.exception("Background job %s failed", job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.message = "任务失败"
                job.completed_at = utcnow()
    finally:
        with _job_lock:
            if _active_job_id == job_id:
                _active_job_id = None
        _release_pipeline_lock(job_id)


def _start_job(kind: Literal["daily", "sync", "analyze"]) -> dict[str, Any]:
    global _active_job_id
    initialize_application()
    with _job_lock:
        if _active_job_id is not None:
            raise HTTPException(status_code=409, detail="已有抓取或分析任务正在运行")
        try:
            with session_scope() as db:
                existing = db.scalar(
                    select(Job)
                    .where(Job.status.in_(RUNNING_STATUSES))
                    .order_by(desc(Job.created_at))
                )
                if existing:
                    heartbeat = _as_utc(existing.started_at or existing.created_at)
                    if heartbeat and heartbeat < utcnow() - timedelta(hours=2):
                        existing.status = "failed"
                        existing.error = "应用上次退出后遗留的任务已自动关闭"
                        existing.message = "任务超时"
                        existing.completed_at = utcnow()
                    else:
                        _active_job_id = existing.id
                        raise HTTPException(
                            status_code=409,
                            detail="已有抓取或分析任务正在运行",
                        )
                job_id = str(uuid4())
                if not _acquire_pipeline_lock(job_id):
                    raise HTTPException(
                        status_code=409,
                        detail="已有抓取或分析任务正在运行",
                    )
                db.add(Job(id=job_id, kind=kind, status="queued", message="任务已排队"))
        except Exception:
            if "job_id" in locals():
                _release_pipeline_lock(job_id)
            raise
        _active_job_id = job_id
    asyncio.create_task(_run_job(job_id, kind))
    return {"job_id": job_id, "status": "queued", "message": "任务已在后台启动"}


def _provider_public_status(db: Session) -> dict[str, Any]:
    settings = _setting_map(db)
    public = provider_status()
    if settings.get("provider_name"):
        public["provider_name"] = settings["provider_name"]
    if settings.get("base_url"):
        public["base_url"] = settings["base_url"]
    if settings.get("model"):
        public["model"] = settings["model"]
    if settings.get("api_key"):
        public.update(
            api_key_configured=True,
            mock_mode=False,
            key_source="database",
            config_source="database",
        )
    return public


@app.get("/api/health")
@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    db.scalar(select(func.count()).select_from(Profile))
    return {
        "ok": True,
        "status": "healthy",
        "time": utcnow(),
        "provider": _provider_public_status(db),
    }


@app.get("/api/dashboard")
def dashboard(
    target_date: date | None = Query(default=None, alias="date"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    day = target_date or date.today()
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    paper_day = func.coalesce(Paper.published_at, Paper.first_seen_at)
    day_condition = (paper_day >= start) & (paper_day < end)
    fetched_today = (
        db.scalar(
            select(func.count(Paper.id))
            .join(PaperMatch, PaperMatch.paper_id == Paper.id)
            .where(PaperMatch.profile_id == profile.id, day_condition)
        )
        or 0
    )
    screened_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screened_at.is_not(None),
                day_condition,
            )
        )
        or 0
    )
    high_priority_today = (
        db.scalar(
            select(func.count(PaperMatch.id))
            .join(Paper, Paper.id == PaperMatch.paper_id)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.recommendation_score >= 80,
                day_condition,
            )
        )
        or 0
    )
    papers = (
        db.scalars(
            _query_papers(db)
            .join(PaperMatch, PaperMatch.paper_id == Paper.id)
            .where(PaperMatch.profile_id == profile.id)
            .order_by(desc(PaperMatch.recommendation_score), desc(Paper.published_at))
            .limit(100)
        )
        .unique()
        .all()
    )
    profile_topics = list(profile.topics)
    taxonomy_fingerprint = direction_taxonomy_fingerprint(profile_topics)
    day_papers = [
        paper
        for paper in papers
        if start <= (_as_utc(paper.published_at or paper.first_seen_at) or start) < end
    ]
    visible = day_papers or papers
    items = [
        _paper_wire(
            paper,
            profile.id,
            topics=profile_topics,
            taxonomy_fingerprint=taxonomy_fingerprint,
        )
        for paper in visible
    ]
    topics = Counter(direction for item in items for direction in item["matched_topics"])
    latest_daily_job = db.scalar(
        select(Job)
        .where(Job.kind == "daily", Job.status == "completed")
        .order_by(desc(Job.completed_at), desc(Job.created_at))
    )
    latest_result = latest_daily_job.result if latest_daily_job is not None else {}
    current_settings = _setting_map(db)
    recent_cutoff = utcnow() - timedelta(days=7)
    pending_screening = (
        db.scalar(
            select(func.count(ScreeningQueueItem.id)).where(
                ScreeningQueueItem.profile_id == profile.id,
                ScreeningQueueItem.status.in_(("queued", "processing", "failed")),
            )
        )
        or 0
    )
    deep_threshold = float(current_settings.get("deep_relevance_threshold", 90))
    pending_deep = (
        db.scalar(
            select(func.count(PaperMatch.id)).where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screened_at.is_not(None),
                PaperMatch.relevance_score >= deep_threshold,
                PaperMatch.paper.has(Paper.last_seen_at >= recent_cutoff),
                ~PaperMatch.paper.has(
                    Paper.analyses.any(
                        (Analysis.profile_id == profile.id)
                        & (Analysis.kind == "deep")
                        & (Analysis.status == "completed")
                    )
                ),
            )
        )
        or 0
    )
    active_job = db.scalar(
        select(Job).where(Job.status.in_(RUNNING_STATUSES)).order_by(desc(Job.created_at))
    )
    latest_digest = db.scalar(
        select(Digest).where(Digest.profile_id == profile.id).order_by(desc(Digest.digest_date))
    )
    return {
        "date": day,
        "total_papers": fetched_today or len(visible),
        "screened_papers": screened_today,
        "high_priority_papers": high_priority_today,
        "processing_summary": {
            "latest_update_at": (
                _as_utc(latest_daily_job.completed_at) if latest_daily_job is not None else None
            ),
            "latest_fetched_count": int(latest_result.get("fetched", 0) or 0),
            "latest_screened_count": int(latest_result.get("screened", 0) or 0),
            "latest_deep_analyzed_count": int(latest_result.get("analyzed", 0) or 0),
            "pending_screening_count": pending_screening,
            "pending_deep_count": pending_deep,
            "screening_limit": int(
                current_settings.get("screen_batch_size", current_settings.get("screen_top_n", 30))
            ),
            "screening_lexical_threshold": LEXICAL_FLOORS[
                recall_mode(current_settings.get("semantic_recall_mode"))
            ],
            "screening_queue_total": int(latest_result.get("screening_queue_total", 0) or 0),
            "screening_completed": int(latest_result.get("screening_completed", 0) or 0),
            "screening_failed": int(latest_result.get("screening_failed", 0) or 0),
            "screening_current_batch": int(latest_result.get("screening_batches", 0) or 0),
            "screening_total_batches": int(latest_result.get("screening_total_batches", 0) or 0),
            "report_incomplete": bool(latest_result.get("report_incomplete", False)),
            "deep_analysis_limit": int(current_settings.get("deep_analysis_top_n", 5)),
            "deep_relevance_threshold": deep_threshold,
        },
        "items": items,
        "topic_stats": [{"topic": name, "count": count} for name, count in topics.most_common()],
        "profile": _profile_wire(
            profile,
            deep_threshold,
            _clean_string_list(current_settings.get("author_watchlist", [])),
        ),
        "settings": _settings_wire(db),
        "stats": {
            "fetched_today": fetched_today,
            "relevant_today": high_priority_today,
            "analyzed_today": sum(1 for paper in visible if paper.analyses),
            "saved_total": db.scalar(
                select(func.count()).select_from(Feedback).where(Feedback.action == "save")
            )
            or 0,
        },
        "recommendations": [
            {
                "paper": item,
                "recommendation": {
                    "paper_id": item["id"],
                    "score": item["recommendation_score"],
                    "explanation": item["why_recommended"] or "符合研究画像",
                    "matched_topics": item["match_reasons"],
                    "matched_keywords": item["match_reasons"],
                },
            }
            for item in items
        ],
        "latest_digest": _digest_wire(latest_digest),
        "active_job": _job_wire(active_job),
        "provider": _provider_public_status(db),
    }


@app.get("/api/papers")
def papers(
    q: str | None = None,
    category: str | None = None,
    priority: Literal["high", "medium", "low"] | None = None,
    feedback: str | None = None,
    reading_state: ReadingStateValue | None = None,
    sort: Literal["recommendation", "relevance", "time"] = "recommendation",
    min_score: float = Query(default=0, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    score = func.coalesce(
        PaperMatch.recommendation_score,
        PaperMatch.relevance_score,
        PaperMatch.lexical_score,
        0,
    )
    conditions = [
        PaperMatch.profile_id == profile.id,
        PaperMatch.screened_at.is_not(None),
        score >= min_score,
    ]
    if q:
        term = f"%{q.strip()}%"
        conditions.append(
            or_(
                Paper.title.ilike(term),
                Paper.abstract.ilike(term),
                Paper.arxiv_id.ilike(term),
                Paper.authors_json.contains(q.strip()),
            )
        )
    if category:
        conditions.append(Paper.categories_json.contains(category))
    if priority == "high":
        conditions.append(score >= 80)
    elif priority == "medium":
        conditions.extend((score >= 60, score < 80))
    elif priority == "low":
        conditions.append(score < 60)
    if reading_state == "unread":
        conditions.append(
            or_(
                ~Paper.reading_states.any(PaperReadingState.profile_id == profile.id),
                Paper.reading_states.any(
                    (PaperReadingState.profile_id == profile.id)
                    & (PaperReadingState.state == "unread")
                ),
            )
        )
    elif reading_state is not None:
        conditions.append(
            Paper.reading_states.any(
                (PaperReadingState.profile_id == profile.id)
                & (PaperReadingState.state == reading_state)
            )
        )

    statement = (
        _query_papers(db).join(PaperMatch, PaperMatch.paper_id == Paper.id).where(*conditions)
    )
    count_statement = (
        select(func.count(func.distinct(Paper.id)))
        .select_from(Paper)
        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
        .where(*conditions)
    )
    if feedback:
        statement = statement.join(Feedback, Feedback.paper_id == Paper.id).where(
            Feedback.profile_id == profile.id, Feedback.action == feedback
        )
        count_statement = count_statement.join(Feedback, Feedback.paper_id == Paper.id).where(
            Feedback.profile_id == profile.id, Feedback.action == feedback
        )
    if sort == "time":
        ordering = (desc(Paper.published_at), desc(score), Paper.id)
    elif sort == "relevance":
        ordering = (desc(PaperMatch.relevance_score), desc(Paper.published_at), Paper.id)
    else:
        ordering = (desc(score), desc(Paper.published_at), Paper.id)
    statement = statement.order_by(*ordering).offset(offset).limit(limit)
    total = int(db.scalar(count_statement) or 0)
    category_rows = db.scalars(
        select(Paper.categories_json)
        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
        .where(
            PaperMatch.profile_id == profile.id,
            PaperMatch.screened_at.is_not(None),
        )
    ).all()
    available_categories = sorted(
        {value for values in category_rows for value in list(values or [])},
        key=str.casefold,
    )
    profile_topics = list(profile.topics)
    taxonomy_fingerprint = direction_taxonomy_fingerprint(profile_topics)
    result = [
        _paper_wire(
            item,
            profile.id,
            topics=profile_topics,
            taxonomy_fingerprint=taxonomy_fingerprint,
        )
        for item in db.scalars(statement).unique().all()
    ]
    return {
        "items": result,
        "count": len(result),
        "total": total,
        "offset": offset,
        "limit": limit,
        "page": offset // limit + 1,
        "pages": max(1, (total + limit - 1) // limit),
        "has_previous": offset > 0,
        "has_next": offset + len(result) < total,
        "scope": "screened",
        "categories": available_categories,
    }


@app.get("/api/papers/{paper_id}")
def paper_detail(paper_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _active_profile(db)
    paper = db.scalar(_query_papers(db).where(Paper.id == paper_id))
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    profile_topics = list(profile.topics)
    return _paper_wire(
        paper,
        profile.id,
        detail=True,
        topics=profile_topics,
        taxonomy_fingerprint=direction_taxonomy_fingerprint(profile_topics),
    )


@app.post("/api/papers/{paper_id}/feedback")
def save_feedback(
    paper_id: int, payload: FeedbackPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
    if db.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    action = "skip" if payload.action in ("skip", "dismiss") else payload.action
    item = db.scalar(
        select(Feedback).where(Feedback.paper_id == paper_id, Feedback.profile_id == profile.id)
    )
    if item is None:
        item = Feedback(paper_id=paper_id, profile_id=profile.id)
        db.add(item)
    item.action = action
    item.rating = payload.rating
    item.note = payload.note
    requested_reasons = list(dict.fromkeys(payload.reasons))
    current_reasons = {reason.code: reason for reason in item.reasons}
    for code, reason in current_reasons.items():
        if code not in requested_reasons:
            db.delete(reason)
    for code in requested_reasons:
        if code not in current_reasons:
            item.reasons.append(FeedbackReason(code=code))
    _sync_favorites_membership(db, profile.id, paper_id, saved=action == "save")
    db.commit()
    return {
        "ok": True,
        "id": item.id,
        "paper_id": paper_id,
        "action": item.action,
        "note": item.note,
        "rating": item.rating,
        "reasons": requested_reasons,
    }


@app.patch("/api/papers/{paper_id}/reading")
def update_paper_reading(
    paper_id: int, payload: ReadingPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
    if db.get(Paper, paper_id) is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    item = db.scalar(
        select(PaperReadingState).where(
            PaperReadingState.paper_id == paper_id,
            PaperReadingState.profile_id == profile.id,
        )
    )
    if item is None:
        item = PaperReadingState(paper_id=paper_id, profile_id=profile.id)
        db.add(item)
    if payload.state is not None:
        item.state = payload.state
    elif payload.reading_state is not None:
        item.state = payload.reading_state
    if payload.priority is not None:
        item.priority = payload.priority
    if payload.notes is not None:
        item.notes = payload.notes
    if payload.tags is not None:
        item.tags = _clean_tags(payload.tags)
    db.commit()
    db.refresh(item)
    return {"ok": True, "paper_id": paper_id, **_reading_wire(item)}


def _project_or_404(db: Session, project_id: int, profile_id: int) -> ResearchProject:
    project = db.scalar(
        select(ResearchProject)
        .options(selectinload(ResearchProject.paper_links).selectinload(ProjectPaper.paper))
        .where(
            ResearchProject.id == project_id,
            ResearchProject.profile_id == profile_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="分类库不存在")
    return project


@app.get("/api/projects")
def list_projects(
    project_status: ProjectStatusValue | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    _ensure_favorites_library(db, profile.id)
    db.commit()
    statement = (
        select(ResearchProject)
        .options(selectinload(ResearchProject.paper_links))
        .where(ResearchProject.profile_id == profile.id)
        .order_by(
            desc(ResearchProject.kind == FAVORITES_LIBRARY_KIND),
            desc(ResearchProject.updated_at),
            ResearchProject.id,
        )
    )
    if project_status is not None:
        statement = statement.where(ResearchProject.status == project_status)
    projects = db.scalars(statement).unique().all()
    return {"items": [_project_wire(project) for project in projects], "count": len(projects)}


@app.post("/api/projects", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreatePayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _active_profile(db)
    name = payload.name.strip()
    duplicate = db.scalar(
        select(ResearchProject.id).where(
            ResearchProject.profile_id == profile.id,
            func.lower(ResearchProject.name) == name.casefold(),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同名分类库已存在")
    project = ResearchProject(
        profile_id=profile.id,
        name=name,
        description=payload.description.strip(),
        status=payload.status,
        color=payload.color.lower(),
        goal=payload.goal.strip(),
        due_date=payload.due_date,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_wire(project, detail=True)


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _active_profile(db)
    return _project_wire(_project_or_404(db, project_id, profile.id), detail=True)


@app.patch("/api/projects/{project_id}")
def update_project(
    project_id: int, payload: ProjectUpdatePayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    if project.kind == FAVORITES_LIBRARY_KIND:
        raise HTTPException(status_code=409, detail="收藏库是系统分类，不能编辑")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        name = changes["name"].strip()
        duplicate = db.scalar(
            select(ResearchProject.id).where(
                ResearchProject.profile_id == profile.id,
                ResearchProject.id != project.id,
                func.lower(ResearchProject.name) == name.casefold(),
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="同名分类库已存在")
        project.name = name
    for field in ("description", "status", "color", "goal", "due_date"):
        if field not in changes:
            continue
        value = changes[field]
        if value is None and field != "due_date":
            continue
        if field in ("description", "goal"):
            value = value.strip()
        if field == "color":
            value = value.lower()
        setattr(project, field, value)
    db.commit()
    return _project_wire(_project_or_404(db, project_id, profile.id), detail=True)


@app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> Response:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    if project.kind == FAVORITES_LIBRARY_KIND:
        raise HTTPException(status_code=409, detail="收藏库是系统分类，不能删除")
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/projects/{project_id}/papers")
def save_project_paper(
    project_id: int, payload: ProjectPaperPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    paper = db.get(Paper, payload.paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    link = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id,
            ProjectPaper.paper_id == paper.id,
        )
    )
    created = link is None
    if link is None:
        link = ProjectPaper(project_id=project.id, paper_id=paper.id)
        db.add(link)
    link.reading_state = payload.reading_state
    link.priority = payload.priority
    link.notes = payload.notes.strip()
    link.tags = _clean_tags(payload.tags)
    if project.kind == FAVORITES_LIBRARY_KIND:
        _set_saved_feedback(db, profile.id, paper.id, True)
    db.commit()
    saved = db.scalar(
        select(ProjectPaper)
        .options(joinedload(ProjectPaper.project), joinedload(ProjectPaper.paper))
        .where(ProjectPaper.id == link.id)
    )
    assert saved is not None
    return {"ok": True, "created": created, **_project_membership_wire(saved, include_paper=True)}


def _save_project_paper_by_id(
    db: Session,
    project: ResearchProject,
    paper: Paper,
    payload: ProjectPaperUpdatePayload | None,
    *,
    require_existing: bool,
) -> tuple[ProjectPaper, bool]:
    link = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id,
            ProjectPaper.paper_id == paper.id,
        )
    )
    created = link is None
    if link is None:
        if require_existing:
            raise HTTPException(status_code=404, detail="论文不在该分类库中")
        link = ProjectPaper(project_id=project.id, paper_id=paper.id)
        db.add(link)
    if payload is not None:
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("reading_state") is not None:
            link.reading_state = changes["reading_state"]
        if changes.get("priority") is not None:
            link.priority = changes["priority"]
        if changes.get("notes") is not None:
            link.notes = changes["notes"].strip()
        if changes.get("tags") is not None:
            link.tags = _clean_tags(changes["tags"])
    if project.kind == FAVORITES_LIBRARY_KIND:
        _set_saved_feedback(db, project.profile_id, paper.id, True)
    db.commit()
    saved = db.scalar(
        select(ProjectPaper)
        .options(joinedload(ProjectPaper.project), joinedload(ProjectPaper.paper))
        .where(ProjectPaper.id == link.id)
    )
    assert saved is not None
    return saved, created


@app.post("/api/projects/{project_id}/papers/{paper_id}")
def add_project_paper_by_id(
    project_id: int,
    paper_id: int,
    payload: ProjectPaperUpdatePayload | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    link, created = _save_project_paper_by_id(db, project, paper, payload, require_existing=False)
    return {"ok": True, "created": created, **_project_membership_wire(link, include_paper=True)}


@app.patch("/api/projects/{project_id}/papers/{paper_id}")
def update_project_paper_by_id(
    project_id: int,
    paper_id: int,
    payload: ProjectPaperUpdatePayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    link, _ = _save_project_paper_by_id(db, project, paper, payload, require_existing=True)
    return {"ok": True, "created": False, **_project_membership_wire(link, include_paper=True)}


@app.delete("/api/projects/{project_id}/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_paper(project_id: int, paper_id: int, db: Session = Depends(get_db)) -> Response:
    profile = _active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    link = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id,
            ProjectPaper.paper_id == paper_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="论文不在该分类库中")
    if project.kind == FAVORITES_LIBRARY_KIND:
        _set_saved_feedback(db, profile.id, paper_id, False)
    db.delete(link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/profile")
def get_profile(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = _setting_map(db)
    return _profile_wire(
        _active_profile(db),
        float(settings.get("deep_relevance_threshold", 90)),
        _clean_string_list(settings.get("author_watchlist", [])),
    )


@app.get("/api/directions/catalog")
def get_direction_catalog() -> dict[str, Any]:
    """Return the versioned suggestion catalog without changing the profile."""

    return direction_catalog_payload()


@app.put("/api/profile")
def update_profile(
    payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
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
        _set_setting(db, "deep_relevance_threshold", threshold)
    if validated.author_watchlist is not None:
        _set_setting(
            db,
            "author_watchlist",
            _clean_string_list(validated.author_watchlist),
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
    settings = _setting_map(db)
    return _profile_wire(
        _active_profile(db),
        float(settings.get("deep_relevance_threshold", 90)),
        _clean_string_list(settings.get("author_watchlist", [])),
    )


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return _settings_wire(db)


@app.put("/api/settings")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    mapping = {
        "provider": "provider_name",
        "provider_name": "provider_name",
        "refresh_time": "schedule_time",
        "schedule_time": "schedule_time",
        "max_candidates": "max_results",
        "max_results": "max_results",
        "deep_analysis_limit": "deep_analysis_top_n",
        "deep_analysis_top_n": "deep_analysis_top_n",
        "deep_relevance_threshold": "deep_relevance_threshold",
        "screen_top_n": "screen_batch_size",
        "screen_batch_size": "screen_batch_size",
        "semantic_recall_mode": "semantic_recall_mode",
        "retention_days": "retention_days",
        "analysis_budget_mode": "analysis_budget_mode",
        "daily_ai_synthesis_enabled": "daily_ai_synthesis_enabled",
        "analysis_prompt_template_id": "analysis_prompt_template_id",
        "screening_prompt": "screening_prompt",
        "deep_prompt": "deep_prompt",
        "base_url": "base_url",
        "model": "model",
        "api_key": "api_key",
        "timezone": "timezone",
        "email_enabled": "email_enabled",
        "smtp_host": "smtp_host",
        "smtp_port": "smtp_port",
        "smtp_security": "smtp_security",
        "smtp_username": "smtp_username",
        "smtp_password": "smtp_password",
        "smtp_from": "smtp_from",
        "email_recipients": "email_recipients",
        "public_base_url": "public_base_url",
    }
    for source, target in mapping.items():
        if source not in values:
            continue
        value = values[source]
        if target in {"api_key", "smtp_password"} and value == "":
            setting = db.get(AppSetting, target)
            if setting:
                db.delete(setting)
        elif target not in {"api_key", "smtp_password"} or value:
            _set_setting(db, target, value)
    if "screen_top_n" in values or "screen_batch_size" in values:
        compatible_batch_size = values.get(
            "screen_batch_size", values.get("screen_top_n", 30)
        )
        _set_setting(
            db, "screen_top_n", int(compatible_batch_size)
        )
    db.commit()
    return _settings_wire(db)


@app.get("/api/prompts/catalog")
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


@app.get("/api/methods/catalog")
def methods_catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = _setting_map(db)
    return method_catalog_payload(_clean_custom_methods(settings.get("custom_method_terms")))


@app.get("/api/methods/candidates")
def method_candidates(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = _setting_map(db)
    window_days = int(settings.get("method_candidate_window_days", METHOD_CANDIDATE_WINDOW_DAYS))
    min_papers = int(settings.get("method_candidate_min_papers", METHOD_CANDIDATE_MIN_PAPERS))
    custom = _clean_custom_methods(settings.get("custom_method_terms"))
    dismissed = _clean_string_list(settings.get("dismissed_method_candidates", []))
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


@app.post("/api/methods/candidates/accept")
def accept_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = _setting_map(db)
    candidates = method_candidates(db)["items"]
    candidate = next(
        (item for item in candidates if item["key"] == normalize_method_key(payload.key)),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="该候选已不存在或不再满足重复门槛")
    name = str(payload.name or candidate["name"]).strip()
    custom = _clean_custom_methods(settings.get("custom_method_terms"))
    if normalize_method_key(name) in {normalize_method_key(item["name"]) for item in custom}:
        raise HTTPException(status_code=409, detail="自定义方法中已经存在同名标签")
    aliases = _clean_string_list(
        [candidate["name"], *payload.aliases] if name != candidate["name"] else payload.aliases,
        limit=20,
    )
    custom.append({"name": name, "aliases": aliases})
    _set_setting(db, "custom_method_terms", custom)
    db.commit()
    return method_catalog_payload(custom)


@app.post("/api/methods/candidates/dismiss")
def dismiss_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = _setting_map(db)
    dismissed = _clean_string_list(settings.get("dismissed_method_candidates", []))
    key = normalize_method_key(payload.key)
    if key and key not in {normalize_method_key(item) for item in dismissed}:
        dismissed.append(key)
    _set_setting(db, "dismissed_method_candidates", dismissed)
    db.commit()
    return {"ok": True, "dismissed_key": key}


@app.put("/api/methods/custom")
def replace_custom_methods(
    payload: CustomMethodsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    custom = _clean_custom_methods([item.model_dump() for item in payload.items])
    _set_setting(db, "custom_method_terms", custom)
    db.commit()
    return method_catalog_payload(custom)


@app.post("/api/settings/test")
async def test_provider(
    payload: ProviderTestPayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    settings = _setting_map(db)
    key = payload.api_key or settings.get("api_key") or get_config().api_key
    base_url = (payload.base_url or settings.get("base_url") or get_config().base_url).rstrip("/")
    model = payload.model or settings.get("model") or get_config().model
    if not key:
        return {
            "ok": True,
            "message": "未配置 API Key，当前将使用本地演示分析",
            "provider_name": "mock",
            "model": "mock",
            "transport": "mock",
        }
    try:
        from .services.analyzer import OpenAICompatibleAdapter

        config = replace(
            get_config(),
            provider_name=payload.provider
            or payload.provider_name
            or settings.get("provider_name")
            or "openai-compatible",
            base_url=base_url,
            model=model,
            api_key=key,
        )
        result = await OpenAICompatibleAdapter(config).test_connection()
        return result.model_dump()
    except Exception as exc:
        return {
            "ok": False,
            "message": f"连接失败：{exc}",
            "provider_name": payload.provider or "openai-compatible",
            "model": model,
            "transport": "http",
        }


@app.post("/api/settings/embedding/test")
async def test_embedding_model() -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(embedding_status, warm=True)
        return {"ok": True, **result}
    except (SemanticEmbeddingError, OSError, ValueError) as exc:
        return {"ok": False, "message": str(exc), **embedding_status()}


@app.post("/api/settings/email/test")
async def test_email_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = _active_profile(db)
    settings = _setting_map(db)
    selected_date = current_date_in_timezone(str(settings.get("timezone", "Asia/Shanghai")))
    snapshot = _persist_daily_research_report(
        db,
        profile.id,
        report_date=selected_date,
        timezone_name=str(settings.get("timezone", "Asia/Shanghai")),
        refresh=False,
    )
    db.commit()
    result = await _send_daily_report_email(
        snapshot.report_date,
        delivery_kind=f"test-{uuid4().hex[:12]}",
        require_enabled=False,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}


@app.post("/api/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh() -> dict[str, Any]:
    return _start_job("daily")


@app.post("/api/run-daily", status_code=status.HTTP_202_ACCEPTED)
async def start_daily() -> dict[str, Any]:
    return _start_job("daily")


@app.post("/api/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync() -> dict[str, Any]:
    return _start_job("sync")


@app.post("/api/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze() -> dict[str, Any]:
    return _start_job("analyze")


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _job_wire(job) or {}


@app.get("/api/status")
def status_view(db: Session = Depends(get_db)) -> dict[str, Any]:
    active = db.scalar(
        select(Job).where(Job.status.in_(RUNNING_STATUSES)).order_by(desc(Job.created_at))
    )
    latest = db.scalar(select(Job).order_by(desc(Job.created_at)))
    return {
        "busy": active is not None,
        "active_job": _job_wire(active),
        "latest_job": _job_wire(latest),
        "provider": _provider_public_status(db),
    }


def _digest_wire(digest: Digest | None) -> dict[str, Any] | None:
    if digest is None:
        return None
    return {
        "id": digest.id,
        "profile_id": digest.profile_id,
        "digest_date": digest.digest_date,
        "title": digest.title,
        "summary": digest.summary,
        "paper_ids": digest.paper_ids,
        "stats": digest.stats,
        "status": digest.status,
        "created_at": digest.created_at,
    }


@app.get("/api/digest/preview")
def digest_preview(
    target_date: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = _active_profile(db)
    day = target_date or date.today()
    digest = db.scalar(
        select(Digest).where(Digest.profile_id == profile.id, Digest.digest_date == day)
    )
    if digest is None:
        papers = (
            db.scalars(
                _query_papers(db)
                .join(PaperMatch, PaperMatch.paper_id == Paper.id)
                .where(PaperMatch.profile_id == profile.id)
                .order_by(desc(PaperMatch.recommendation_score))
                .limit(10)
            )
            .unique()
            .all()
        )
        return {
            "digest_date": day,
            "title": f"{day.isoformat()} arXiv 每日精选（预览）",
            "summary": f"当前有 {len(papers)} 篇候选论文。",
            "items": [
                _paper_wire(
                    paper,
                    profile.id,
                    topics=list(profile.topics),
                    taxonomy_fingerprint=direction_taxonomy_fingerprint(list(profile.topics)),
                )
                for paper in papers
            ],
            "persisted": False,
        }
    paper_ids = digest.paper_ids
    papers = (
        db.scalars(_query_papers(db).where(Paper.id.in_(paper_ids))).unique().all()
        if paper_ids
        else []
    )
    by_id = {paper.id: paper for paper in papers}
    profile_topics = list(profile.topics)
    taxonomy_fingerprint = direction_taxonomy_fingerprint(profile_topics)
    return {
        **(_digest_wire(digest) or {}),
        "items": [
            _paper_wire(
                by_id[item],
                profile.id,
                topics=profile_topics,
                taxonomy_fingerprint=taxonomy_fingerprint,
            )
            for item in paper_ids
            if item in by_id
        ],
        "persisted": True,
    }


@app.get("/api/trends/topics")
def topic_trends(
    as_of: date | None = Query(default=None, alias="date"),
    limit: int = Query(default=12, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    timezone_name = str(_setting_map(db).get("timezone", "Asia/Shanghai"))
    return build_topic_trends(
        db,
        profile.id,
        as_of=as_of,
        timezone_name=timezone_name,
        limit=limit,
    )


@app.get("/api/trends/roadmap")
def research_roadmap(
    as_of: date | None = Query(default=None, alias="date"),
    days: int = Query(default=90, ge=7, le=730),
    max_nodes: int = Query(default=30, ge=5, le=60),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    timezone_name = str(_setting_map(db).get("timezone", "Asia/Shanghai"))
    return build_research_roadmap(
        db,
        profile.id,
        as_of=as_of,
        timezone_name=timezone_name,
        days=days,
        max_nodes=max_nodes,
    )


@app.get("/api/reports/daily/history")
def daily_research_report_history(
    limit: int = Query(default=60, ge=1, le=3650),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = _active_profile(db)
    snapshots = db.scalars(
        select(DailyResearchReport)
        .where(DailyResearchReport.profile_id == profile.id)
        .order_by(desc(DailyResearchReport.report_date))
        .limit(limit)
    ).all()
    items = [
        {
            "id": snapshot.id,
            "report_date": snapshot.report_date,
            "title": snapshot.payload.get("title", snapshot.report_date.isoformat()),
            "coverage": snapshot.payload.get("coverage", {}),
            "generated_at": _as_utc(snapshot.created_at),
            "updated_at": _as_utc(snapshot.updated_at),
        }
        for snapshot in snapshots
    ]
    return {"items": items, "count": len(items)}


def _get_or_refresh_daily_research_report(
    db: Session,
    *,
    report_date: date | None,
    max_papers: int,
    refresh: bool,
) -> dict[str, Any]:
    profile = _active_profile(db)
    timezone_name = str(_setting_map(db).get("timezone", "Asia/Shanghai"))
    selected_date = report_date
    if selected_date is None:
        selected_date = db.scalar(
            select(Digest.digest_date)
            .where(Digest.profile_id == profile.id)
            .order_by(desc(Digest.digest_date))
            .limit(1)
        )
    snapshot = _persist_daily_research_report(
        db,
        profile.id,
        report_date=selected_date,
        timezone_name=timezone_name,
        max_papers=max_papers,
        refresh=refresh,
    )
    db.commit()
    db.refresh(snapshot)
    return _daily_report_snapshot_wire(snapshot)


@app.get("/api/reports/daily")
def daily_research_report(
    report_date: date | None = Query(default=None, alias="date"),
    max_papers: int = Query(default=10, ge=1, le=30),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _get_or_refresh_daily_research_report(
        db,
        report_date=report_date,
        max_papers=max_papers,
        refresh=refresh,
    )


@app.post("/api/reports/daily/regenerate")
def regenerate_daily_research_report(
    report_date: date | None = Query(default=None, alias="date"),
    max_papers: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _get_or_refresh_daily_research_report(
        db,
        report_date=report_date,
        max_papers=max_papers,
        refresh=True,
    )


@app.post("/api/reports/daily/synthesize")
async def synthesize_daily_research_report(
    report_date: date = Query(alias="date"),
    force: bool = Query(default=True),
) -> dict[str, Any]:
    await _synthesize_daily_research_report(
        report_date,
        force=force,
        respect_enabled=False,
    )
    with SessionLocal() as db:
        profile = _active_profile(db)
        snapshot = db.scalar(
            select(DailyResearchReport).where(
                DailyResearchReport.profile_id == profile.id,
                DailyResearchReport.report_date == report_date,
            )
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="研究日报不存在")
        return _daily_report_snapshot_wire(snapshot)


@app.post("/api/reports/daily/send")
async def send_daily_research_report(
    report_date: date = Query(alias="date"),
) -> dict[str, Any]:
    result = await _send_daily_report_email(
        report_date,
        delivery_kind=f"manual-{uuid4().hex[:10]}",
        require_enabled=False,
    )
    return {"ok": result.get("email_failed", 0) == 0 and result.get("email_sent", 0) > 0, **result}


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        requested = (FRONTEND_DIST / path).resolve()
        try:
            requested.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            raise HTTPException(status_code=404) from None
        if path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def frontend_fallback() -> str:
        return """<!doctype html>
        <meta charset="utf-8"><title>XivPulse</title>
        <style>
        body { font-family: system-ui; max-width: 720px; margin: 80px auto; padding: 24px; }
        code { background: #eef1f4; padding: 2px 6px; }
        </style>
        <h1>XivPulse 后端已启动</h1>
        <p>尚未找到前端构建文件。请重新运行 <code>.\\start.ps1</code>，或进入
        <code>frontend</code> 执行 <code>npm install</code> 和 <code>npm run build</code>。</p>
        <p><a href="/docs">打开 API 文档</a></p>"""


__all__ = ["app", "initialize_application", "run_daily_pipeline"]
