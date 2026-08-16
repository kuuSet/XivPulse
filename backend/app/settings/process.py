"""Immutable settings read once from the process environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _string_tuple(value: Any) -> tuple[str, ...]:
    values = value.split(",") if isinstance(value, str) else value
    if not isinstance(values, list | tuple | set):
        raise ValueError("expected a comma-separated string or a sequence")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = str(item).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    provider_name: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_mode: Literal["responses", "chat"] = "responses"
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("XIVPULSE_API_KEY", "OPENAI_API_KEY"),
        repr=False,
        exclude=True,
    )
    request_timeout: float = Field(default=180.0, ge=5.0, le=600.0)

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

    @field_validator("base_url")
    @classmethod
    def normalise_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def mock_mode(self) -> bool:
        return not bool(self.api_key)

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        payload["api_key_configured"] = not self.mock_mode
        payload["mock_mode"] = self.mock_mode
        return payload


class ProcessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    data_dir: Path = Path("data")
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    trusted_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    embedding_backfill: bool = True
    auth_enabled: bool = False
    admin_username: str = ""
    admin_password_hash: str = Field(default="", repr=False, exclude=True)
    session_secret: str = Field(default="", repr=False, exclude=True)
    secure_cookies: bool | None = None
    trust_proxy: bool = False

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def parse_trusted_hosts(cls, value: Any) -> tuple[str, ...]:
        return _string_tuple(value)

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'xivpulse.db').as_posix()}"

    @property
    def model_cache(self) -> Path:
        return self.data_dir / "model-cache"


def _build_process_settings() -> ProcessSettings:
    process = ProcessSettings()
    secure_cookies = (
        process.auth_enabled if process.secure_cookies is None else process.secure_cookies
    )
    return process.model_copy(
        update={
            "secure_cookies": secure_cookies,
        }
    )


@lru_cache(maxsize=1)
def get_process_settings() -> ProcessSettings:
    return _build_process_settings()


__all__ = [
    "ProcessSettings",
    "ProviderSettings",
    "get_process_settings",
]
