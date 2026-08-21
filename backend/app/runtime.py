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
    with session_factory() as db:
        init_db(db.get_bind())
    with session_scope(session_factory) as db:
        seed_defaults(db)
        recover_stale_jobs(db)
