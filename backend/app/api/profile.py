"""Research profile and direction catalog HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Topic
from ..schemas import ProfileUpdate
from ..services.directions.catalog import direction_catalog_payload
from ..services.state import active_profile, clean_string_list
from ..settings import load_runtime_settings, set_setting
from .presenters.profiles import _profile_wire

router = APIRouter()


@router.get("/api/profile")
def get_profile(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return _profile_wire(active_profile(db), settings)


@router.get("/api/directions/catalog")
def get_direction_catalog() -> dict[str, Any]:
    """Return the versioned suggestion catalog without changing the profile."""

    return direction_catalog_payload()


@router.put("/api/profile")
def update_profile(
    payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    if "topics" not in payload:
        raise HTTPException(
            status_code=422,
            detail="保存研究画像必须提交完整 topics；旧版单方向格式已停用，以免覆盖正式研究方向",
        )
    try:
        validated = ProfileUpdate.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        message = str(errors[0].get("msg", "研究画像设置无效")) if errors else "研究画像设置无效"
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        raise HTTPException(status_code=422, detail=message) from exc
    if "min_relevance" in payload:
        threshold = float(payload["min_relevance"])
        if not 0 <= threshold <= 100:
            raise HTTPException(status_code=422, detail="最低相关度必须在 0 到 100 之间")
        set_setting(db, "deep_analysis_threshold", threshold)
    for field in ("watched_authors", "watched_organizations"):
        value = getattr(validated, field)
        if value is not None:
            set_setting(db, field, clean_string_list(value))
    for field in (
        "prioritize_watched_authors",
        "prioritize_watched_organizations",
    ):
        value = getattr(validated, field)
        if value is not None:
            set_setting(db, field, value)
    for field in ("name", "description", "language", "active"):
        value = getattr(validated, field)
        if value is not None:
            setattr(profile, field, value)
    if validated.topics is not None:
        existing = {topic.id: topic for topic in profile.topics}
        retained: set[int] = set()
        for value in validated.topics:
            topic = existing.get(value.id) if value.id else None
            if topic is None:
                topic = Topic(profile_id=profile.id)
                db.add(topic)
            else:
                retained.add(topic.id)
            for key, item in value.model_dump(exclude={"id"}).items():
                setattr(topic, key, item)
        for topic in profile.topics:
            if topic.id not in retained and topic.id in existing:
                db.delete(topic)
    db.commit()
    db.refresh(profile)
    settings = load_runtime_settings(db)
    return _profile_wire(active_profile(db), settings)
