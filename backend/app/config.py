"""Typed application configuration loaded from process environment and persisted settings."""

from __future__ import annotations

import json
import os
import re
import threading
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
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


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    provider: ProviderConfig
    schedule_time: str = "13:30"
    timezone: str = "Asia/Shanghai"
    max_results: int = 1000
    screen_batch_size: int = 30
    semantic_recall_mode: Literal["high", "balanced", "precise"] = "high"
    deep_analysis_top_n: int = 5
    deep_relevance_threshold: float = 90.0
    retention_days: int = 365
    analysis_budget_mode: Literal["economy", "balanced", "deep"] = "balanced"
    daily_ai_synthesis_enabled: bool = True
    analysis_prompt_template_id: str = "balanced-review"
    screening_prompt: str | None = None
    deep_prompt: str | None = None
    custom_method_terms: tuple[Any, ...] = ()
    dismissed_method_candidates: tuple[str, ...] = ()
    method_candidate_window_days: int = 30
    method_candidate_min_papers: int = 2
    author_watchlist: tuple[str, ...] = ()
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False, compare=False)
    smtp_from: str = ""
    email_recipients: tuple[str, ...] = ()
    public_base_url: str = ""

    def prompt_values(self) -> dict[str, Any]:
        return {
            "analysis_prompt_template_id": self.analysis_prompt_template_id,
            "screening_prompt": self.screening_prompt,
            "deep_prompt": self.deep_prompt,
        }


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


def _setting_values(db: Session) -> dict[str, Any]:
    from .models import AppSetting

    return {item.key: item.value for item in db.scalars(select(AppSetting)).all()}


def _configured_value(
    values: Mapping[str, Any],
    key: str,
    default: Any,
    *,
    environment_name: str | None = None,
) -> Any:
    environment = _environment(environment_name or key.upper())
    if environment is not None:
        return environment
    return values.get(key, default)


def _bounded_int(
    values: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(_configured_value(values, key, default))
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _bounded_float(
    values: Mapping[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(_configured_value(values, key, default))
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _configured_bool(values: Mapping[str, Any], key: str, default: bool) -> bool:
    value = _configured_value(values, key, default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _configured_text(values: Mapping[str, Any], key: str, default: str = "") -> str:
    value = _configured_value(values, key, default)
    return str(value).strip() if value is not None else default


def _optional_text(values: Mapping[str, Any], key: str) -> str | None:
    value = _configured_value(values, key, None)
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list | tuple | set):
        values = value
    else:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = str(item).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def _effective_provider(values: Mapping[str, Any], process: ProcessConfig) -> ProviderConfig:
    configured = process.provider
    provider_environment = any(
        _nonempty_environment(name)
        for name in ("PROVIDER_NAME", "BASE_URL", "MODEL", "API_KEY")
    )
    environment_key = bool(
        _nonempty_environment("API_KEY") or _string_or_none(os.getenv("OPENAI_API_KEY"))
    )
    database_provider = any(
        values.get(key) for key in ("provider_name", "base_url", "model", "api_key")
    )

    def database_or_process(key: str, environment_name: str, fallback: Any) -> Any:
        if _nonempty_environment(environment_name):
            return fallback
        return values.get(key) or fallback

    api_key = (
        configured.api_key
        if environment_key
        else values.get("api_key") or configured.api_key
    )
    source = (
        configured.config_source
        if provider_environment
        else "database"
        if database_provider
        else configured.config_source
    )
    key_source = (
        configured.key_source
        if environment_key or not values.get("api_key")
        else "database"
    )
    return replace(
        configured,
        provider_name=str(
            database_or_process("provider_name", "PROVIDER_NAME", configured.provider_name)
        ),
        base_url=_normalise_base_url(
            str(database_or_process("base_url", "BASE_URL", configured.base_url))
        ),
        model=str(database_or_process("model", "MODEL", configured.model)),
        api_key=api_key,
        config_source=source,
        key_source=key_source,
    )


def load_runtime_settings(
    db: Session,
    *,
    process: ProcessConfig | None = None,
) -> RuntimeSettings:
    process = process or get_process_config()
    values = _setting_values(db)
    schedule_time = _configured_text(values, "schedule_time", "13:30")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        schedule_time = "13:30"
    recall = _configured_text(values, "semantic_recall_mode", "high").casefold()
    if recall not in {"high", "balanced", "precise"}:
        recall = "high"
    budget = _configured_text(values, "analysis_budget_mode", "balanced").casefold()
    if budget not in {"economy", "balanced", "deep"}:
        budget = "balanced"
    security = _configured_text(values, "smtp_security", "starttls").casefold()
    if security not in {"starttls", "ssl"}:
        security = "starttls"
    custom_methods = values.get("custom_method_terms", [])
    dismissed = values.get("dismissed_method_candidates", [])
    return RuntimeSettings(
        provider=_effective_provider(values, process),
        schedule_time=schedule_time,
        timezone=_configured_text(values, "timezone", "Asia/Shanghai"),
        max_results=_bounded_int(values, "max_results", 1000, 1, 1000),
        screen_batch_size=_bounded_int(values, "screen_batch_size", 30, 0, 500),
        semantic_recall_mode=cast(Literal["high", "balanced", "precise"], recall),
        deep_analysis_top_n=_bounded_int(values, "deep_analysis_top_n", 5, 0, 100),
        deep_relevance_threshold=_bounded_float(
            values, "deep_relevance_threshold", 90.0, 0.0, 100.0
        ),
        retention_days=_bounded_int(values, "retention_days", 365, 1, 3650),
        analysis_budget_mode=cast(Literal["economy", "balanced", "deep"], budget),
        daily_ai_synthesis_enabled=_configured_bool(
            values, "daily_ai_synthesis_enabled", True
        ),
        analysis_prompt_template_id=_configured_text(
            values, "analysis_prompt_template_id", "balanced-review"
        ),
        screening_prompt=_optional_text(values, "screening_prompt"),
        deep_prompt=_optional_text(values, "deep_prompt"),
        custom_method_terms=(
            tuple(custom_methods) if isinstance(custom_methods, list | tuple) else ()
        ),
        dismissed_method_candidates=_string_tuple(dismissed),
        method_candidate_window_days=_bounded_int(
            values, "method_candidate_window_days", 30, 1, 365
        ),
        method_candidate_min_papers=_bounded_int(
            values, "method_candidate_min_papers", 2, 2, 100
        ),
        author_watchlist=_string_tuple(values.get("author_watchlist", [])),
        email_enabled=_configured_bool(values, "email_enabled", False),
        smtp_host=_configured_text(values, "smtp_host"),
        smtp_port=_bounded_int(values, "smtp_port", 587, 1, 65535),
        smtp_security=cast(Literal["starttls", "ssl"], security),
        smtp_username=_configured_text(values, "smtp_username"),
        smtp_password=_configured_text(values, "smtp_password"),
        smtp_from=_configured_text(values, "smtp_from"),
        email_recipients=_string_tuple(
            _configured_value(values, "email_recipients", [])
        ),
        public_base_url=_configured_text(values, "public_base_url").rstrip("/"),
    )


def setting_value(db: Session, key: str, default: Any = None) -> Any:
    from .models import AppSetting

    setting = db.get(AppSetting, key)
    return setting.value if setting is not None else default


def set_setting(db: Session, key: str, value: Any) -> None:
    from .models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=value))
    else:
        setting.value = value


def delete_setting(db: Session, key: str) -> None:
    from .models import AppSetting

    setting = db.get(AppSetting, key)
    if setting is not None:
        db.delete(setting)


def provider_status(config: ProviderConfig | None = None) -> dict[str, Any]:
    config = config or get_process_config().provider
    return config.public_dict()


__all__ = [
    "ProcessConfig",
    "ProviderConfig",
    "RuntimeSettings",
    "delete_setting",
    "get_process_config",
    "load_runtime_settings",
    "provider_status",
    "set_setting",
    "setting_value",
]
