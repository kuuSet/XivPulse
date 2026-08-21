from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
from app import create_app
from app.database import Base, make_session_factory
from app.database_defaults import seed_defaults
from app.pipeline import PipelineCoordinator
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def app_factory() -> Iterator[Callable[[], FastAPI]]:
    engines: list[Engine] = []

    def build() -> FastAPI:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        engines.append(engine)
        session_factory = make_session_factory(engine)
        Base.metadata.create_all(engine)
        with session_factory() as session:
            seed_defaults(session)
        return create_app(
            session_factory=session_factory,
            pipeline=Mock(spec=PipelineCoordinator),
            startup=lambda: None,
            frontend_dist=None,
        )

    yield build

    for engine in engines:
        engine.dispose()


@pytest.fixture()
def app(app_factory: Callable[[], FastAPI]) -> FastAPI:
    return app_factory()


@pytest.fixture()
def session_factory(app: FastAPI) -> sessionmaker[Session]:
    return app.state.session_factory


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
