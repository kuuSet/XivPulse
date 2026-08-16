"""Typed settings resolved from environment, database values, and defaults."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy import select

from .process import ProcessConfig, ProviderConfig, _string_tuple, get_process_config

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

StringTuple = Annotated[tuple[str, ...], NoDecode]
JsonTuple = Annotated[tuple[Any, ...], NoDecode]


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    provider: ProviderConfig
    schedule_time: str = Field(default="13:30", pattern=r"(?:[01]\d|2[0-3]):[0-5]\d")
    timezone: str = "Asia/Shanghai"
    max_results: int = Field(default=1000, ge=1, le=1000)
    screen_batch_size: int = Field(default=30, ge=0, le=500)
    semantic_recall_mode: Literal["high", "balanced", "precise"] = "high"
    deep_analysis_top_n: int = Field(default=5, ge=0, le=100)
    deep_relevance_threshold: float = Field(default=90.0, ge=0.0, le=100.0)
    retention_days: int = Field(default=365, ge=1, le=3650)
    analysis_budget_mode: Literal["economy", "balanced", "deep"] = "balanced"
    daily_ai_synthesis_enabled: bool = True
    analysis_prompt_template_id: str = "balanced-review"
    screening_prompt: str | None = None
    deep_prompt: str | None = None
    custom_method_terms: JsonTuple = ()
    dismissed_method_candidates: StringTuple = ()
    method_candidate_window_days: int = Field(default=30, ge=1, le=365)
    method_candidate_min_papers: int = Field(default=2, ge=2, le=100)
    author_watchlist: StringTuple = ()
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = Field(default="", repr=False, exclude=True)
    smtp_from: str = ""
    email_recipients: StringTuple = ()
    public_base_url: str = ""

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

    @field_validator(
        "dismissed_method_candidates",
        "author_watchlist",
        "email_recipients",
        mode="before",
    )
    @classmethod
    def parse_string_tuple(cls, value: Any) -> tuple[str, ...]:
        return _string_tuple(value)

    @field_validator("custom_method_terms", mode="before")
    @classmethod
    def parse_custom_method_terms(cls, value: Any) -> tuple[Any, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("expected a JSON array") from exc
        if not isinstance(value, list | tuple):
            raise ValueError("expected a sequence")
        return tuple(value)

    @field_validator("screening_prompt", "deep_prompt", mode="before")
    @classmethod
    def empty_prompt_is_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("public_base_url")
    @classmethod
    def normalise_public_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    def prompt_values(self) -> dict[str, Any]:
        return {
            "analysis_prompt_template_id": self.analysis_prompt_template_id,
            "screening_prompt": self.screening_prompt,
            "deep_prompt": self.deep_prompt,
        }


def _setting_values(db: Session) -> dict[str, Any]:
    from ..models import AppSetting

    return {item.key: item.value for item in db.scalars(select(AppSetting)).all()}


def _effective_provider(values: dict[str, Any], process: ProcessConfig) -> ProviderConfig:
    configured = process.provider
    database_provider = any(
        values.get(key) for key in ("provider_name", "base_url", "model", "api_key")
    )
    provider_environment = any(
        os.getenv(f"XIVPULSE_{name}")
        for name in ("PROVIDER_NAME", "BASE_URL", "MODEL", "API_KEY")
    )
    environment_key = bool(os.getenv("XIVPULSE_API_KEY") or os.getenv("OPENAI_API_KEY"))
    return ProviderConfig(
        provider_name=values.get("provider_name") or configured.provider_name,
        base_url=values.get("base_url") or configured.base_url,
        model=values.get("model") or configured.model,
        api_mode=configured.api_mode,
        api_key=(
            configured.api_key
            if environment_key
            else values.get("api_key") or configured.api_key
        ),
        config_source=(
            configured.config_source
            if provider_environment
            else "database"
            if database_provider
            else configured.config_source
        ),
        key_source=(
            configured.key_source
            if environment_key or not values.get("api_key")
            else "database"
        ),
        request_timeout=configured.request_timeout,
    )


def load_runtime_settings(
    db: Session,
    *,
    process: ProcessConfig | None = None,
) -> RuntimeSettings:
    process = process or get_process_config()
    values = _setting_values(db)
    return RuntimeSettings(provider=_effective_provider(values, process), **values)


def setting_value(db: Session, key: str, default: Any = None) -> Any:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    return setting.value if setting is not None else default


def set_setting(db: Session, key: str, value: Any) -> None:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=value))
    else:
        setting.value = value


def delete_setting(db: Session, key: str) -> None:
    from ..models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is not None:
        db.delete(setting)


__all__ = [
    "RuntimeSettings",
    "delete_setting",
    "load_runtime_settings",
    "set_setting",
    "setting_value",
]
