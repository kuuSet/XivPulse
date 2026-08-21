"""Initialize the database state required by every process."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .database import SessionLocal, init_db, session_scope
from .jobs.service import recover_stale_jobs
from .models import Profile, ResearchProject, Topic
from .modules.interests.presets import initial_topic_values
from .services.auth import auth_settings
from .services.library import (
    FAVORITES_LIBRARY_COLOR,
    FAVORITES_LIBRARY_DESCRIPTION,
    FAVORITES_LIBRARY_KIND,
    FAVORITES_LIBRARY_NAME,
)


def bootstrap_application(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    auth_settings()
    with session_factory() as session:
        init_db(session.get_bind())
    with session_scope(session_factory) as session:
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
        recover_stale_jobs(session)


__all__ = ["bootstrap_application"]
