import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import app.jobs.workflow as workflow
import pytest
from app import create_app
from app.database import Base, make_engine, make_session_factory
from app.database_defaults import seed_defaults
from app.models import Job
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def test_apps_use_independent_databases(app_factory: Callable[[], FastAPI]) -> None:
    first_app = app_factory()
    second_app = app_factory()

    with TestClient(first_app) as first, TestClient(second_app) as second:
        response = first.put("/api/settings", json={"prefilter_batch_size": 47})

        assert response.status_code == 200
        assert first.get("/api/settings").json()["prefilter_batch_size"] == 47
        assert second.get("/api/settings").json()["prefilter_batch_size"] == 30


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
    for engine, factory in zip(engines, factories, strict=True):
        Base.metadata.create_all(engine)
        with factory() as session:
            seed_defaults(session)

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
