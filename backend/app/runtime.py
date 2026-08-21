"""Application initialization shared by web and scheduler entrypoints."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from .database import SessionLocal, init_db, session_scope
from .database_defaults import seed_defaults
from .jobs.service import recover_stale_jobs
from .services.auth import auth_settings


def initialize_application(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    auth_settings()
    init_db(session_factory.kw["bind"], seed=False)
    with session_scope(session_factory) as db:
        seed_defaults(db)
        recover_stale_jobs(db)
