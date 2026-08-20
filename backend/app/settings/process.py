"""Immutable settings read once from the process environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class ProviderSettings(BaseSettings):
    # Defines how Pydantic reads environment variables and validates this group.
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    # Selects the model connection used by analysis jobs.
    model_source: Literal["api", "codex"] = "api"

    # API provider and model selection.
    provider_name: str = "openai"
    model_name: str = "gpt-4o-mini"

    # Model API connection and authentication.
    api_base_url: str = "https://api.openai.com/v1"
    api_protocol: Literal["responses", "chat"] = "responses"
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("XIVPULSE_API_KEY", "OPENAI_API_KEY"),
        repr=False,
        exclude=True,
    )
    api_timeout_seconds: float = Field(default=180.0, ge=5.0, le=600.0)

    # Optional Codex model; an empty value uses the Codex CLI default.
    codex_model: str = ""

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

    @field_validator("api_base_url")
    @classmethod
    def normalise_api_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def configured(self) -> bool:
        return self.model_source == "codex" or bool(self.api_key)

    @property
    def selected_model(self) -> str:
        if self.model_source == "codex":
            return self.codex_model or "default"
        return self.model_name

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        payload["api_key_configured"] = bool(self.api_key)
        return payload


class ProcessSettings(BaseSettings):
    # Defines how Pydantic reads environment variables and validates this group.
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    # Persistent application data.
    data_dir: Path = Path("data")

    # Model provider settings applied at process startup.
    provider: ProviderSettings = Field(default_factory=ProviderSettings)

    # Optional read-only Codex login file mounted into the process.
    codex_auth_file: Path | None = Field(default=None, repr=False, exclude=True)

    # Server request filtering.
    server_allowed_hosts: tuple[str, ...] = ()

    # Single-user server authentication.
    auth_enabled: bool = False
    auth_username: str = ""
    auth_password_hash: str = Field(default="", repr=False, exclude=True)
    auth_session_secret: str = Field(default="", repr=False, exclude=True)
    auth_secure_cookies: bool | None = None
    auth_trust_proxy: bool = False

    @field_validator("data_dir", "codex_auth_file")
    @classmethod
    def resolve_path(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'xivpulse.db').as_posix()}"


@lru_cache(maxsize=1)
def get_process_settings() -> ProcessSettings:
    process = ProcessSettings()
    auth_secure_cookies = (
        process.auth_enabled if process.auth_secure_cookies is None else process.auth_secure_cookies
    )
    return process.model_copy(
        update={
            "auth_secure_cookies": auth_secure_cookies,
        }
    )


__all__ = [
    "ProcessSettings",
    "ProviderSettings",
    "get_process_settings",
]
