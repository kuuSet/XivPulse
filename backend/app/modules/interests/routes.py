"""Followed-interest HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import Topic
from ...services.state import active_profile
from ...settings import load_runtime_settings, set_setting
from .presets import interest_presets_payload
from .schemas import InterestsUpdate
from .views import interests_view

router = APIRouter()


@router.get("/api/interests")
def get_interests(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return interests_view(active_profile(db), settings)


@router.get("/api/interests/presets")
def get_interest_presets() -> dict[str, Any]:
    return interest_presets_payload()


@router.put("/api/interests")
def update_interests(
    payload: InterestsUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    for field in ("watched_authors", "watched_organizations"):
        set_setting(db, field, getattr(payload, field))
    existing = {topic.id: topic for topic in profile.topics}
    retained: set[int] = set()
    for value in payload.topics:
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
    return interests_view(active_profile(db), settings)
