"""Immutable configuration read once from the process environment."""

from __future__ import annotations

import json
import os
import threading
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB_PATH = (_REPO_ROOT / "data" / "xivpulse.db").resolve()
_DEFAULT_MODEL_CACHE = (_REPO_ROOT / "data" / "model-cache").resolve()
_CODEX_DIR = Path.home() / ".codex"
_CODEX_CONFIG_PATH = _CODEX_DIR / "config.toml"
_CODEX_AUTH_PATH = _CODEX_DIR / "auth.json"
_CONFIG_LOCK = threading.Lock()
_CACHED_PROCESS_CONFIG: ProcessConfig | None = None


def _environment(name: str) -> str | None:
    return os.getenv(f"XIVPULSE_{name}")


def _nonempty_environment(name: str) -> str | None:
    value = _environment(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _environment_enabled(name: str, default: bool = False) -> bool:
    value = _environment(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


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


def _normalise_base_url(value: str | None) -> str:
    return (value or "https://api.openai.com/v1").strip().rstrip("/")


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider_name: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_mode: Literal["responses", "chat"] = "responses"
    api_key: str | None = field(default=None, repr=False, compare=False)
    config_source: str = "defaults"
    key_source: str = "none"
    request_timeout: float = 180.0

    @property
    def mock_mode(self) -> bool:
        return not bool(self.api_key)

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("api_key", None)
        payload["api_key_configured"] = not self.mock_mode
        payload["mock_mode"] = self.mock_mode
        return payload


@dataclass(frozen=True, slots=True)
class ProcessConfig:
    database_url: str
    provider: ProviderConfig
    host: str = "127.0.0.1"
    port: int = 8765
    trusted_hosts: tuple[str, ...] = ()
    model_cache: Path = _DEFAULT_MODEL_CACHE
    embedding_backfill: bool = True
    auth_enabled: bool = False
    admin_username: str = ""
    admin_password_hash: str = field(default="", repr=False, compare=False)
    session_secret: str = field(default="", repr=False, compare=False)
    secure_cookies: bool = False
    trust_proxy: bool = False


def _build_provider_config() -> ProviderConfig:
    import_codex = _environment_enabled("IMPORT_CODEX_CONFIG")
    codex = _load_codex_config() if import_codex else {}
    codex_provider_name = str(codex.get("model_provider") or "openai")
    providers = codex.get("model_providers")
    codex_provider: dict[str, Any] = {}
    if isinstance(providers, dict):
        candidate = providers.get(codex_provider_name)
        if isinstance(candidate, dict):
            codex_provider = candidate

    env_provider_name = _nonempty_environment("PROVIDER_NAME")
    env_base_url = _nonempty_environment("BASE_URL")
    env_model = _nonempty_environment("MODEL")
    explicit_key = _nonempty_environment("API_KEY")
    openai_key = _string_or_none(os.getenv("OPENAI_API_KEY"))
    codex_key = (
        _load_codex_api_key() if import_codex and not explicit_key and not openai_key else None
    )
    api_key = explicit_key or openai_key or codex_key
    if explicit_key:
        key_source = "environment:XIVPULSE_API_KEY"
    elif openai_key:
        key_source = "environment:OPENAI_API_KEY"
    elif codex_key:
        key_source = "codex-auth"
    else:
        key_source = "none"

    provider_name = env_provider_name or codex_provider_name
    base_url = _normalise_base_url(
        env_base_url or _string_or_none(codex_provider.get("base_url"))
    )
    model = env_model or _string_or_none(codex.get("model")) or "gpt-4o-mini"
    wire_api = (_string_or_none(codex_provider.get("wire_api")) or "responses").lower()
    api_mode: Literal["responses", "chat"] = "chat" if wire_api == "chat" else "responses"
    source = "codex" if import_codex else "defaults"
    if env_provider_name or env_base_url or env_model or explicit_key:
        source = "environment+codex" if import_codex else "environment"

    try:
        timeout = float(_nonempty_environment("REQUEST_TIMEOUT") or "180")
    except ValueError:
        timeout = 180.0
    return ProviderConfig(
        provider_name=provider_name,
        base_url=base_url,
        model=model,
        api_mode=api_mode,
        api_key=api_key,
        config_source=source,
        key_source=key_source,
        request_timeout=min(max(timeout, 5.0), 600.0),
    )


def _build_process_config() -> ProcessConfig:
    database_url = _nonempty_environment("DATABASE_URL") or (
        f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"
    )
    try:
        port = int(_nonempty_environment("PORT") or "8765")
    except ValueError:
        port = 8765
    if not 1 <= port <= 65535:
        port = 8765
    auth_enabled = _environment_enabled("AUTH_ENABLED")
    trusted_hosts = tuple(
        value.strip()
        for value in str(_environment("TRUSTED_HOSTS") or "").split(",")
        if value.strip()
    )
    model_cache = _nonempty_environment("MODEL_CACHE")
    return ProcessConfig(
        database_url=database_url,
        provider=_build_provider_config(),
        host=_nonempty_environment("HOST") or "127.0.0.1",
        port=port,
        trusted_hosts=trusted_hosts,
        model_cache=(
            Path(model_cache).expanduser().resolve() if model_cache else _DEFAULT_MODEL_CACHE
        ),
        embedding_backfill=_environment_enabled("EMBEDDING_BACKFILL", default=True),
        auth_enabled=auth_enabled,
        admin_username=str(_environment("ADMIN_USERNAME") or "").strip(),
        admin_password_hash=str(_environment("ADMIN_PASSWORD_HASH") or "").strip(),
        session_secret=str(_environment("SESSION_SECRET") or ""),
        secure_cookies=_environment_enabled("SECURE_COOKIES", default=auth_enabled),
        trust_proxy=_environment_enabled("TRUST_PROXY"),
    )


def get_process_config(*, refresh: bool = False) -> ProcessConfig:
    global _CACHED_PROCESS_CONFIG
    with _CONFIG_LOCK:
        if refresh or _CACHED_PROCESS_CONFIG is None:
            _CACHED_PROCESS_CONFIG = _build_process_config()
        return _CACHED_PROCESS_CONFIG


def provider_status(config: ProviderConfig | None = None) -> dict[str, Any]:
    return (config or get_process_config().provider).public_dict()


__all__ = [
    "ProcessConfig",
    "ProviderConfig",
    "get_process_config",
    "provider_status",
]
