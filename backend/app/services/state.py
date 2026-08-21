"""Shared access to the single-user data scope."""

from __future__ import annotations

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
        raise RuntimeError("关注设置尚未初始化")
    return profile
