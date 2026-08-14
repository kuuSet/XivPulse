"""Application configuration and conservative local Codex provider discovery."""

from __future__ import annotations

import json
import threading
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .env import env_value, xivpulse_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
_XIVPULSE_DB_PATH = (_REPO_ROOT / "data" / "xivpulse.db").resolve()
_LEGACY_DB_PATH = (_REPO_ROOT / "data" / "arxiv_daily.db").resolve()
_DEFAULT_DB_PATH = (
    _LEGACY_DB_PATH
    if _LEGACY_DB_PATH.exists() and not _XIVPULSE_DB_PATH.exists()
    else _XIVPULSE_DB_PATH
)
_CODEX_DIR = Path.home() / ".codex"
_CODEX_CONFIG_PATH = _CODEX_DIR / "config.toml"
_CODEX_AUTH_PATH = _CODEX_DIR / "auth.json"
_CONFIG_LOCK = threading.Lock()
_CACHED_CONFIG: AppConfig | None = None


def _nonempty_env(*names: str) -> str | None:
    value = env_value(*names)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_codex_config() -> dict[str, Any]:
    """Read only the documented user-level Codex configuration file."""

    try:
        with _CODEX_CONFIG_PATH.open("rb") as handle:
            value = tomllib.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}


def _load_codex_api_key() -> str | None:
    """Read an API key without exposing arbitrary auth fields or file contents."""

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


@dataclass(frozen=True, slots=True)
class AppConfig:
    database_url: str
    host: str = "127.0.0.1"
    port: int = 8765
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
        """Return configuration safe for logs and API responses."""

        payload = asdict(self)
        payload.pop("api_key", None)
        payload["api_key_configured"] = not self.mock_mode
        payload["mock_mode"] = self.mock_mode
        return payload


def _build_config() -> AppConfig:
    import_codex = str(xivpulse_env("IMPORT_CODEX_CONFIG", "false")).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    codex = _load_codex_config() if import_codex else {}
    provider_name = str(codex.get("model_provider") or "openai")
    providers = codex.get("model_providers")
    provider: dict[str, Any] = {}
    if isinstance(providers, dict):
        candidate = providers.get(provider_name)
        if isinstance(candidate, dict):
            provider = candidate

    env_base_url = _nonempty_env("XIVPULSE_BASE_URL", "ARXIV_DAILY_BASE_URL")
    env_model = _nonempty_env("XIVPULSE_MODEL", "ARXIV_DAILY_MODEL")
    base_url = _normalise_base_url(env_base_url or _string_or_none(provider.get("base_url")))
    model = env_model or _string_or_none(codex.get("model")) or "gpt-4o-mini"
    wire_api = (_string_or_none(provider.get("wire_api")) or "responses").lower()
    api_mode: Literal["responses", "chat"] = "chat" if wire_api == "chat" else "responses"

    explicit_key = _nonempty_env("XIVPULSE_API_KEY", "ARXIV_DAILY_API_KEY")
    openai_key = _nonempty_env("OPENAI_API_KEY")
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

    database_url = _nonempty_env("XIVPULSE_DATABASE_URL", "ARXIV_DAILY_DATABASE_URL")
    if not database_url:
        database_url = f"sqlite:///{_DEFAULT_DB_PATH.as_posix()}"

    host = _nonempty_env("XIVPULSE_HOST", "ARXIV_DAILY_HOST") or "127.0.0.1"
    try:
        port = int(_nonempty_env("XIVPULSE_PORT", "ARXIV_DAILY_PORT") or "8765")
    except ValueError:
        port = 8765
    if not 1 <= port <= 65535:
        port = 8765

    try:
        timeout = float(
            _nonempty_env("XIVPULSE_REQUEST_TIMEOUT", "ARXIV_DAILY_REQUEST_TIMEOUT") or "180"
        )
    except ValueError:
        timeout = 180.0
    timeout = min(max(timeout, 5.0), 600.0)

    source = "codex" if import_codex else "defaults"
    if env_base_url or env_model or explicit_key:
        source = "environment+codex" if import_codex else "environment"
    return AppConfig(
        database_url=database_url,
        host=host,
        port=port,
        provider_name=provider_name,
        base_url=base_url,
        model=model,
        api_mode=api_mode,
        api_key=api_key,
        config_source=source,
        key_source=key_source,
        request_timeout=timeout,
    )


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def get_config(*, refresh: bool = False) -> AppConfig:
    global _CACHED_CONFIG
    with _CONFIG_LOCK:
        if refresh or _CACHED_CONFIG is None:
            _CACHED_CONFIG = _build_config()
        return _CACHED_CONFIG


def provider_status(config: AppConfig | None = None) -> dict[str, Any]:
    config = config or get_config()
    return {
        "provider_name": config.provider_name,
        "base_url": config.base_url,
        "model": config.model,
        "api_mode": config.api_mode,
        "api_key_configured": not config.mock_mode,
        "mock_mode": config.mock_mode,
        "config_source": config.config_source,
        "key_source": config.key_source,
    }


__all__ = ["AppConfig", "get_config", "provider_status"]
