"""Followed-topic and direction catalog HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Topic
from ..schemas import InterestsUpdate
from ..services.directions.catalog import direction_catalog_payload
from ..services.state import active_profile, clean_string_list
from ..settings import load_runtime_settings, set_setting
from .presenters.interests import _interests_wire

router = APIRouter()


@router.get("/api/interests")
def get_interests(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return _interests_wire(active_profile(db), settings)


@router.get("/api/directions/catalog")
def get_direction_catalog() -> dict[str, Any]:
    """Return the versioned suggestion catalog without changing followed topics."""

    return direction_catalog_payload()


@router.put("/api/interests")
def update_interests(
    payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    try:
        validated = InterestsUpdate.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        message = str(errors[0].get("msg", "关注设置无效")) if errors else "关注设置无效"
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        raise HTTPException(status_code=422, detail=message) from exc
    for field in ("watched_authors", "watched_organizations"):
        set_setting(db, field, clean_string_list(getattr(validated, field)))
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
    return _interests_wire(active_profile(db), settings)
