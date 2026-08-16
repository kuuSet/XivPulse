"""Typed settings resolved from environment, database values, and defaults."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import select

from .process import (
    ProcessConfig,
    ProviderConfig,
    _environment,
    _nonempty_environment,
    _normalise_base_url,
    _string_or_none,
    get_process_config,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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


def _setting_values(db: Session) -> dict[str, Any]:
    from ..models import AppSetting

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
        configured.api_key if environment_key else values.get("api_key") or configured.api_key
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
