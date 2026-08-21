import asyncio
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import app.bootstrap as bootstrap_module
import app.modules.jobs.workflow as workflow
import pytest
from app.bootstrap import bootstrap_application
from app.database import make_engine, make_session_factory
from app.main import create_app
from app.models import Job, Profile, utcnow
from app.modules.jobs.service import PipelineCoordinator
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def test_apps_use_independent_databases(app_factory: Callable[[], FastAPI]) -> None:
    first_app = app_factory()
    second_app = app_factory()

    with TestClient(first_app) as first, TestClient(second_app) as second:
        response = first.put("/api/settings", json={"prefilter_batch_size": 47})

        assert response.status_code == 200
        assert first.get("/api/settings").json()["prefilter_batch_size"] == 47
        assert second.get("/api/settings").json()["prefilter_batch_size"] == 30


def test_application_lifespan_initializes_its_database(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'application.db').as_posix()}")
    factory = make_session_factory(engine)
    app = create_app(session_factory=factory, frontend_dist=None)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    with factory() as session:
        assert len(session.scalars(select(Profile)).all()) == 1
    engine.dispose()


def test_application_lifespan_closes_its_pipeline(
    tmp_path: Path,
) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'shutdown.db').as_posix()}")
    pipeline = Mock(spec=PipelineCoordinator)
    pipeline.shutdown = AsyncMock()
    app = create_app(
        session_factory=make_session_factory(engine),
        pipeline=pipeline,
        startup=lambda: None,
        frontend_dist=None,
    )

    with TestClient(app):
        pass

    pipeline.shutdown.assert_awaited_once_with()
    engine.dispose()


@pytest.mark.asyncio
async def test_default_pipelines_use_their_application_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engines = [
        make_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
        for name in ("first.db", "second.db")
    ]
    factories = [make_session_factory(engine) for engine in engines]
    for factory in factories:
        bootstrap_application(factory)

    used_factories: list[sessionmaker[Session]] = []

    async def sync_stage(
        *,
        target_date: date | None,
        session_factory: sessionmaker[Session],
        **_: Any,
    ) -> dict[str, Any]:
        used_factories.append(session_factory)
        assert target_date is not None
        return {"fetch_date": target_date.isoformat(), "fetched": 1}

    monkeypatch.setattr(workflow, "sync_papers", sync_stage)
    apps = [
        create_app(
            session_factory=factory,
            startup=lambda: None,
            frontend_dist=None,
        )
        for factory in factories
    ]
    for index, app in enumerate(apps):
        app.state.pipeline._lock_path = tmp_path / f"pipeline-{index}.lock"

    async def run_job(app: FastAPI, factory: sessionmaker[Session], day: date) -> str:
        started = app.state.pipeline.start_job("sync", target_date=day)
        job_id = str(started["id"])
        for _ in range(100):
            await asyncio.sleep(0.01)
            with factory() as session:
                job = session.get(Job, job_id)
                if job is not None and job.status == "completed":
                    return job_id
        pytest.fail("background job did not complete")

    first_job_id = await run_job(apps[0], factories[0], date(2026, 8, 18))
    second_job_id = await run_job(apps[1], factories[1], date(2026, 8, 19))

    assert used_factories == factories
    with factories[0]() as first_db, factories[1]() as second_db:
        assert first_db.get(Job, first_job_id) is not None
        assert first_db.get(Job, second_job_id) is None
        assert second_db.get(Job, first_job_id) is None
        assert second_db.get(Job, second_job_id) is not None

    for engine in engines:
        engine.dispose()


def test_application_bootstrap_rolls_back_failed_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}")
    factory = make_session_factory(engine)

    def fail_after_seed(session: Session) -> None:
        session.flush()
        assert session.scalar(select(Profile)) is not None
        raise RuntimeError("recovery failed")

    monkeypatch.setattr(bootstrap_module, "recover_stale_jobs", fail_after_seed)

    with pytest.raises(RuntimeError, match="recovery failed"):
        bootstrap_application(factory)

    with factory() as session:
        assert session.scalars(select(Profile)).all() == []
    engine.dispose()


def test_application_bootstrap_recovers_only_stale_jobs(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    factory = make_session_factory(engine)
    bootstrap_application(factory)
    with factory() as session:
        session.add_all(
            [
                Job(
                    id="stale-job",
                    kind="daily",
                    status="running",
                    started_at=utcnow() - timedelta(hours=3),
                ),
                Job(
                    id="current-job",
                    kind="daily",
                    status="running",
                    started_at=utcnow(),
                ),
            ]
        )
        session.commit()

    bootstrap_application(factory)

    with factory() as session:
        stale = session.get(Job, "stale-job")
        current = session.get(Job, "current-job")
        assert stale is not None and stale.status == "failed"
        assert stale.message == "任务超时"
        assert current is not None and current.status == "running"
    engine.dispose()
