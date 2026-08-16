"""Immutable configuration read once from the process environment."""

from __future__ import annotations

import json
import os
import tomllib
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = (_REPO_ROOT / "data" / "xivpulse.db").resolve()
_DEFAULT_MODEL_CACHE = (_REPO_ROOT / "data" / "model-cache").resolve()
_CODEX_DIR = Path.home() / ".codex"
_CODEX_CONFIG_PATH = _CODEX_DIR / "config.toml"
_CODEX_AUTH_PATH = _CODEX_DIR / "auth.json"


def _nonempty_environment(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_codex_config() -> dict[str, Any]:
    try:
        with _CODEX_CONFIG_PATH.open("rb") as handle:
            value = tomllib.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}


def _load_codex_api_key() -> str | None:
    try:
        with _CODEX_AUTH_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("OPENAI_API_KEY")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


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


class ProviderConfig(BaseSettings):
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
    config_source: str = "defaults"
    key_source: str = "none"
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


class ProcessConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XIVPULSE_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    database_url: str = f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    trusted_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    model_cache: Path = _DEFAULT_MODEL_CACHE
    embedding_backfill: bool = True
    auth_enabled: bool = False
    admin_username: str = ""
    admin_password_hash: str = Field(default="", repr=False, exclude=True)
    session_secret: str = Field(default="", repr=False, exclude=True)
    secure_cookies: bool | None = None
    trust_proxy: bool = False
    import_codex_config: bool = False

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def parse_trusted_hosts(cls, value: Any) -> tuple[str, ...]:
        return _string_tuple(value)

    @field_validator("model_cache")
    @classmethod
    def resolve_model_cache(cls, value: Path) -> Path:
        return value.expanduser().resolve()


def _build_provider_config(*, import_codex: bool) -> ProviderConfig:
    codex = _load_codex_config() if import_codex else {}
    codex_provider_name = str(codex.get("model_provider") or "openai")
    providers = codex.get("model_providers")
    codex_provider: dict[str, Any] = {}
    if isinstance(providers, dict):
        candidate = providers.get(codex_provider_name)
        if isinstance(candidate, dict):
            codex_provider = candidate

    explicit_key = _nonempty_environment("XIVPULSE_API_KEY")
    openai_key = _nonempty_environment("OPENAI_API_KEY")
    codex_key = (
        _load_codex_api_key()
        if import_codex and not explicit_key and not openai_key
        else None
    )
    if explicit_key:
        key_source = "environment:XIVPULSE_API_KEY"
    elif openai_key:
        key_source = "environment:OPENAI_API_KEY"
    elif codex_key:
        key_source = "codex-auth"
    else:
        key_source = "none"

    provider_environment = any(
        _nonempty_environment(f"XIVPULSE_{name}")
        for name in ("PROVIDER_NAME", "BASE_URL", "MODEL", "API_KEY")
    )
    source = "codex" if import_codex else "defaults"
    if provider_environment:
        source = "environment+codex" if import_codex else "environment"

    wire_api = (_string_or_none(codex_provider.get("wire_api")) or "responses").lower()
    return ProviderConfig(
        provider_name=codex_provider_name,
        base_url=_string_or_none(codex_provider.get("base_url"))
        or "https://api.openai.com/v1",
        model=_string_or_none(codex.get("model")) or "gpt-4o-mini",
        api_mode="chat" if wire_api == "chat" else "responses",
        api_key=codex_key,
        config_source=source,
        key_source=key_source,
    )


def _build_process_config() -> ProcessConfig:
    process = ProcessConfig()
    secure_cookies = (
        process.auth_enabled if process.secure_cookies is None else process.secure_cookies
    )
    return process.model_copy(
        update={
            "provider": _build_provider_config(import_codex=process.import_codex_config),
            "secure_cookies": secure_cookies,
        }
    )


@lru_cache(maxsize=1)
def _cached_process_config() -> ProcessConfig:
    return _build_process_config()


def get_process_config(*, refresh: bool = False) -> ProcessConfig:
    if refresh:
        _cached_process_config.cache_clear()
    return _cached_process_config()


def provider_status(config: ProviderConfig | None = None) -> dict[str, Any]:
    return (config or get_process_config().provider).public_dict()


__all__ = [
    "ProcessConfig",
    "ProviderConfig",
    "get_process_config",
    "provider_status",
]
