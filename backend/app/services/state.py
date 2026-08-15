"""Shared access to persisted application settings and the active profile."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import AppConfig, get_config
from ..models import AppSetting, Profile


def setting_map(db: Session) -> dict[str, Any]:
    return {item.key: item.value for item in db.scalars(select(AppSetting)).all()}


def set_setting(db: Session, key: str, value: Any) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value_json=value))
    else:
        setting.value = value


def configured_provider_config(settings: dict[str, Any]) -> AppConfig:
    """Overlay database provider settings without exposing stored credentials."""

    config = get_config()
    return replace(
        config,
        provider_name=str(settings.get("provider_name") or config.provider_name),
        base_url=str(settings.get("base_url") or config.base_url),
        model=str(settings.get("model") or config.model),
        api_key=settings.get("api_key") or config.api_key,
        config_source=(
            "database"
            if any(key in settings for key in ("base_url", "model", "api_key"))
            else config.config_source
        ),
        key_source="database" if settings.get("api_key") else config.key_source,
    )


def active_profile(db: Session) -> Profile:
    profile = db.scalar(
        select(Profile)
        .options(selectinload(Profile.topics))
        .where(Profile.active.is_(True))
        .order_by(Profile.id)
    )
    if profile is None:
        profile = db.scalar(
            select(Profile).options(selectinload(Profile.topics)).order_by(Profile.id)
        )
    if profile is None:
        raise HTTPException(status_code=500, detail="研究画像尚未初始化")
    return profile


def clean_string_list(value: Any, *, limit: int = 200) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
