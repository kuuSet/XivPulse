"""Typed settings resolved from environment, database values, and defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from ..integrations.codex.catalog import CodexModel, CodexReasoningEffort
from ..services.methods.types import CustomMethod
from ..services.prompt_catalog import DEFAULT_PROMPT_TEMPLATE_ID, PROMPT_TEMPLATE_IDS
from .process import (
    ProcessSettings,
    ProviderSettings,
    get_process_settings,
    normalize_api_base_url,
)
from .store import load_setting_values

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


ModelStage = Literal["prefilter", "screening", "deep", "synthesis"]


def validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"未知的 IANA 时区：{value}") from exc
    return value


class StageProviders(BaseModel):
    model_config = ConfigDict(frozen=True)

    prefilter: ProviderSettings
    screening: ProviderSettings
    deep: ProviderSettings
    synthesis: ProviderSettings

    def for_stage(self, stage: ModelStage) -> ProviderSettings:
        return getattr(self, stage)


class ProviderOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_source: Literal["api", "codex"] | None = None
    provider_name: str | None = None
    model_name: str | None = Field(default=None, max_length=200)
    api_base_url: str | None = Field(default=None, max_length=2000)
    api_protocol: Literal["responses", "chat"] | None = None
    api_key: str | None = Field(default=None, max_length=10000, repr=False)
    codex_model: CodexModel | None = None
    codex_reasoning_effort: CodexReasoningEffort | None = None

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str | None) -> str | None:
        return normalize_api_base_url(value) if value is not None else None


_PROVIDER_OVERRIDES = TypeAdapter(dict[ModelStage, ProviderOverride])


class RuntimeSettings(BaseSettings):
    # Defines how Pydantic merges environment, database, and default values.
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    # Effective model connection for each LLM stage.
    providers: StageProviders

    # Interface base font size in points.
    ui_font_size_pt: float = Field(default=12.0, ge=10.0, le=16.0, multiple_of=0.5)

    # Daily job schedule.
    schedule_time: str = Field(default="13:30", pattern=r"(?:[01]\d|2[0-3]):[0-5]\d")
    schedule_timezone: str = "Asia/Shanghai"

    # arXiv synchronization limits.
    arxiv_fetch_limit: int = Field(default=1000, ge=1, le=1000)

    # Number of paper titles sent in one prefilter request.
    prefilter_batch_size: int = Field(default=30, ge=1, le=500)

    # Full-text analysis selection.
    deep_analysis_limit: int = Field(default=0, ge=0, le=100)
    deep_analysis_threshold: float = Field(default=90.0, ge=0.0, le=100.0)

    # Model analysis and daily synthesis.
    analysis_budget: Literal["economy", "balanced", "deep"] = "balanced"
    daily_synthesis_enabled: bool = False

    # Analysis prompt selection and overrides.
    prompt_template_id: str = DEFAULT_PROMPT_TEMPLATE_ID
    deep_analysis_prompt: str | None = None

    # Method catalog customization.
    custom_methods: tuple[CustomMethod, ...] = Field(default=(), max_length=100)
    dismissed_method_candidates: tuple[str, ...] = ()

    # Watched authors and organizations.
    watched_authors: tuple[str, ...] = ()
    watched_organizations: tuple[str, ...] = ()

    # Email delivery controls.
    email_enabled: bool = False
    email_recipients: tuple[str, ...] = ()

    # SMTP connection and sender identity.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = Field(default="", repr=False, exclude=True)
    smtp_sender: str = ""

    # Public application URL included in outbound email.
    public_url: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings

    @field_validator("public_url")
    @classmethod
    def normalise_public_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("schedule_timezone")
    @classmethod
    def validate_schedule_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)

    @field_validator("prompt_template_id")
    @classmethod
    def validate_prompt_template(cls, value: str) -> str:
        if value not in PROMPT_TEMPLATE_IDS:
            raise ValueError("未知的论文分析提示词模板")
        return value


def _effective_provider(
    override: ProviderOverride | None,
    process: ProcessSettings,
) -> ProviderSettings:
    configured = process.provider
    values = override or ProviderOverride()
    return ProviderSettings(
        model_source=values.model_source or configured.model_source,
        provider_name=values.provider_name or configured.provider_name,
        model_name=values.model_name or configured.model_name,
        api_base_url=values.api_base_url or configured.api_base_url,
        api_protocol=values.api_protocol or configured.api_protocol,
        api_key=values.api_key or configured.api_key,
        api_timeout_seconds=configured.api_timeout_seconds,
        codex_model=values.codex_model or configured.codex_model,
        codex_reasoning_effort=values.codex_reasoning_effort or configured.codex_reasoning_effort,
    )


def _effective_providers(values: dict[str, Any], process: ProcessSettings) -> StageProviders:
    overrides = _PROVIDER_OVERRIDES.validate_python(values.get("providers", {}))
    return StageProviders(
        **{
            stage: _effective_provider(overrides.get(stage), process)
            for stage in ("prefilter", "screening", "deep", "synthesis")
        }
    )


def load_runtime_settings(
    db: Session,
    *,
    process: ProcessSettings | None = None,
) -> RuntimeSettings:
    process = process or get_process_settings()
    values = load_setting_values(db)
    runtime_values = {key: value for key, value in values.items() if key != "providers"}
    return RuntimeSettings(
        providers=_effective_providers(values, process),
        **runtime_values,
    )


__all__ = [
    "RuntimeSettings",
    "ModelStage",
    "ProviderOverride",
    "StageProviders",
    "load_runtime_settings",
    "validate_timezone_name",
]
