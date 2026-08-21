"""Built-in followed-topic seed data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .modules.interests.presets import initial_topic_values


def seed_defaults(session: Session) -> None:
    from .models import Profile, ResearchProject, Topic
    from .services.library import (
        FAVORITES_LIBRARY_COLOR,
        FAVORITES_LIBRARY_DESCRIPTION,
        FAVORITES_LIBRARY_KIND,
        FAVORITES_LIBRARY_NAME,
    )

    profile = session.scalar(select(Profile).order_by(Profile.id))
    if profile is None:
        profile = Profile()
        session.add(profile)
        session.flush()
        for values in initial_topic_values():
            session.add(Topic(profile_id=profile.id, enabled=True, **values))
        session.add(
            ResearchProject(
                profile_id=profile.id,
                name=FAVORITES_LIBRARY_NAME,
                description=FAVORITES_LIBRARY_DESCRIPTION,
                kind=FAVORITES_LIBRARY_KIND,
                status="active",
                color=FAVORITES_LIBRARY_COLOR,
            )
        )
