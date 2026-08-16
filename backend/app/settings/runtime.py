"""Typed settings resolved from environment, database values, and defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy import select

from .process import ProcessSettings, ProviderSettings, _string_tuple, get_process_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class RuntimeSettings(BaseSettings):
    # Defines how Pydantic merges environment, database, and default values.
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    # Effective model provider settings.
    provider: ProviderSettings

    # Daily job schedule.
    schedule_time: str = Field(default="13:30", pattern=r"(?:[01]\d|2[0-3]):[0-5]\d")
    schedule_timezone: str = "Asia/Shanghai"

    # arXiv synchronization limits.
    arxiv_fetch_limit: int = Field(default=1000, ge=1, le=1000)

    # Candidate screening and semantic recall.
    screening_batch_size: int = Field(default=30, ge=0, le=500)
    semantic_recall_mode: Literal["high", "balanced", "precise"] = "high"

    # Full-text analysis selection.
    deep_analysis_limit: int = Field(default=5, ge=0, le=100)
    deep_analysis_threshold: float = Field(default=90.0, ge=0.0, le=100.0)

    # Model analysis and daily synthesis.
    analysis_budget: Literal["economy", "balanced", "deep"] = "balanced"
    daily_synthesis_enabled: bool = True

    # Analysis prompt selection and overrides.
    prompt_template_id: str = "balanced-review"
    screening_prompt: str | None = None
    deep_analysis_prompt: str | None = None

    # Method catalog customization.
    custom_methods: tuple[Any, ...] = ()
    dismissed_method_candidates: tuple[str, ...] = ()

    # Author-based recall.
    watched_authors: tuple[str, ...] = ()

    # Email delivery controls.
    email_enabled: bool = False
    email_recipients: Annotated[tuple[str, ...], NoDecode] = ()

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

    @field_validator("email_recipients", mode="before")
    @classmethod
    def parse_string_tuple(cls, value: Any) -> tuple[str, ...]:
        return _string_tuple(value)

    @field_validator("public_url")
    @classmethod
    def normalise_public_url(cls, value: str) -> str:
        return value.rstrip("/")

    def prompt_values(self) -> dict[str, Any]:
        return {
            "prompt_template_id": self.prompt_template_id,
            "screening_prompt": self.screening_prompt,
            "deep_analysis_prompt": self.deep_analysis_prompt,
        }


def _setting_values(db: Session) -> dict[str, Any]:
    from ..models import AppSetting

    return {item.key: item.value for item in db.scalars(select(AppSetting)).all()}


def _effective_provider(values: dict[str, Any], process: ProcessSettings) -> ProviderSettings:
    configured = process.provider
    return ProviderSettings(
        provider_name=values.get("provider_name") or configured.provider_name,
        model_name=values.get("model_name") or configured.model_name,
        api_base_url=values.get("api_base_url") or configured.api_base_url,
        api_protocol=configured.api_protocol,
        api_key=values.get("api_key") or configured.api_key,
        api_timeout_seconds=configured.api_timeout_seconds,
    )


def load_runtime_settings(
    db: Session,
    *,
    process: ProcessSettings | None = None,
) -> RuntimeSettings:
    process = process or get_process_settings()
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
