"""Database engine, sessions, and schema initialization."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import get_process_settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_process_settings().database_url
    options: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        parsed = make_url(url)
        if parsed.database and parsed.database != ":memory:":
            database_parent = Path(parsed.database).expanduser().resolve().parent
            database_parent.mkdir(parents=True, exist_ok=True)
    created = create_engine(url, **options)
    if created.dialect.name == "sqlite":

        @event.listens_for(created, "connect")
        def _set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return created


def make_session_factory(bind: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False, future=True)


engine = make_engine()
SessionLocal = make_session_factory(engine)


def init_db(bind: Engine | None = None, *, seed: bool = True) -> Engine:
    from . import models  # noqa: F401
    from .database_defaults import seed_defaults

    target = bind or engine
    Base.metadata.create_all(target)
    if seed:
        factory = make_session_factory(target)
        with factory() as session:
            seed_defaults(session)
    return target


def get_db(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    session = (factory or SessionLocal)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "init_db",
    "make_engine",
    "make_session_factory",
    "session_scope",
]
