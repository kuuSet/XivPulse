"""Shared access to the single-user data scope and normalized string lists."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Profile


def active_profile(db: Session) -> Profile:
    profile = db.scalar(
        select(Profile)
        .options(selectinload(Profile.topics))
        .order_by(Profile.id)
    )
    if profile is None:
        raise HTTPException(status_code=500, detail="关注设置尚未初始化")
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
