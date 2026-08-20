"""Typed settings resolved from environment, database values, and defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .process import ProcessSettings, ProviderSettings, get_process_settings
from .store import load_setting_values

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

    def prompt_values(self) -> dict[str, Any]:
        return {
            "prompt_template_id": self.prompt_template_id,
            "screening_prompt": self.screening_prompt,
            "deep_analysis_prompt": self.deep_analysis_prompt,
        }


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
    values = load_setting_values(db)
    return RuntimeSettings(provider=_effective_provider(values, process), **values)


__all__ = [
    "RuntimeSettings",
    "load_runtime_settings",
]
