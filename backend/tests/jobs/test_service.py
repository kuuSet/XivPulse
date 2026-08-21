from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from app.database import Base, make_engine, make_session_factory
from app.jobs.service import JobConflict, PipelineCoordinator
from app.models import Job
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def job_session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_daily_pipeline_runs_stages_in_order_and_merges_results(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []

    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        calls.append(("sync", target_date))
        return {"fetch_date": "2026-08-14", "fetched": 3}

    async def analyze_stage(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        calls.append(("analyze", (job_id, report_date)))
        return {"prefiltered": 3, "screened": 2}

    async def finalize_daily_stage(
        report_date: date,
        result: dict[str, Any],
        job_id: str | None,
    ) -> dict[str, Any]:
        calls.append(("finalize", (job_id, report_date, dict(result))))
        return {**result, "report_date": report_date.isoformat()}

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=sync_stage,
        analyze_stage=analyze_stage,
        finalize_daily_stage=finalize_daily_stage,
        session_factory=job_session_factory,
        lock_path=tmp_path / "pipeline.lock",
    )

    result = await coordinator.execute("daily", job_id=None, target_date=date(2026, 8, 14))

    assert result == {
        "fetch_date": "2026-08-14",
        "fetched": 3,
        "prefiltered": 3,
        "screened": 2,
        "report_date": "2026-08-14",
    }
    assert calls == [
        ("sync", date(2026, 8, 14)),
        ("analyze", (None, date(2026, 8, 14))),
        (
            "finalize",
            (
                None,
                date(2026, 8, 14),
                {
                    "fetch_date": "2026-08-14",
                    "fetched": 3,
                    "prefiltered": 3,
                    "screened": 2,
                },
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_analyze_pipeline_skips_sync_and_daily_finalization(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    async def unexpected_sync(*, target_date: date | None) -> dict[str, Any]:
        raise AssertionError("sync stage should not run")

    async def analyze_stage(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        assert job_id == "job-1"
        assert report_date is None
        return {"analyzed": 1}

    async def unexpected_finalize(
        _report_date: date,
        _result: dict[str, Any],
        _job_id: str | None,
    ) -> dict[str, Any]:
        raise AssertionError("daily finalization should not run")

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=unexpected_sync,
        analyze_stage=analyze_stage,
        finalize_daily_stage=unexpected_finalize,
        session_factory=job_session_factory,
        lock_path=tmp_path / "pipeline.lock",
    )

    assert await coordinator.execute("analyze", job_id="job-1") == {"analyzed": 1}


@pytest.mark.asyncio
async def test_command_uses_shared_daily_pipeline() -> None:
    from app.jobs import command

    class FakePipeline:
        async def run_daily(self, *, trigger: str) -> dict[str, Any]:
            assert trigger == "scheduled-task"
            return {"accepted": True, "success": True, "fetched": 3, "analyzed": 2}

    assert await command.main(FakePipeline()) == 0


@pytest.mark.asyncio
async def test_successful_job_is_completed_and_releases_lock(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "pipeline.lock"

    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        assert target_date == date(2026, 8, 18)
        return {"fetch_date": "2026-08-18", "fetched": 3}

    async def analyze_stage(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        assert job_id is not None
        assert report_date == date(2026, 8, 18)
        return {"screened": 2}

    async def finalize_stage(
        report_date: date, result: dict[str, Any], job_id: str | None
    ) -> dict[str, Any]:
        assert report_date == date(2026, 8, 18)
        assert job_id is not None
        return {**result, "report_date": report_date.isoformat()}

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=sync_stage,
        analyze_stage=analyze_stage,
        finalize_daily_stage=finalize_stage,
        session_factory=job_session_factory,
        lock_path=lock_path,
    )

    result = await coordinator.run_daily(trigger="test", target_date=date(2026, 8, 18))

    assert result["accepted"] is True
    assert result["success"] is True
    assert result["fetched"] == 3
    assert result["screened"] == 2
    assert not lock_path.exists()
    with job_session_factory() as db:
        job = db.scalar(select(Job))
        assert job is not None
        assert job.status == "completed"
        assert job.progress == 100
        assert job.error is None
        assert job.result == {
            "fetch_date": "2026-08-18",
            "fetched": 3,
            "screened": 2,
            "report_date": "2026-08-18",
        }


@pytest.mark.asyncio
async def test_background_job_uses_the_same_persisted_lifecycle(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        return {"fetch_date": "2026-08-18", "fetched": 1}

    async def unused_analyze(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        raise AssertionError("sync job should not analyze papers")

    async def unused_finalize(
        report_date: date, result: dict[str, Any], job_id: str | None
    ) -> dict[str, Any]:
        raise AssertionError("sync job should not finalize a report")

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=sync_stage,
        analyze_stage=unused_analyze,
        finalize_daily_stage=unused_finalize,
        session_factory=job_session_factory,
        lock_path=tmp_path / "pipeline.lock",
    )

    started = coordinator.start_job("sync", target_date=date(2026, 8, 18))
    for _ in range(100):
        await asyncio.sleep(0.01)
        with job_session_factory() as db:
            job = db.get(Job, started["job_id"])
            if job is not None and job.status == "completed":
                assert job.result == {"fetch_date": "2026-08-18", "fetched": 1}
                break
    else:
        pytest.fail("background job did not complete")


@pytest.mark.asyncio
async def test_failed_job_preserves_error_releases_lock_and_can_run_again(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "pipeline.lock"
    fail = True
    error = "provider failed with full diagnostic: unexpected payload }"

    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        nonlocal fail
        if fail:
            raise RuntimeError(error)
        return {"fetch_date": "2026-08-18", "fetched": 1}

    async def analyze_stage(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        return {"screened": 1}

    async def finalize_stage(
        report_date: date, result: dict[str, Any], job_id: str | None
    ) -> dict[str, Any]:
        return result

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=sync_stage,
        analyze_stage=analyze_stage,
        finalize_daily_stage=finalize_stage,
        session_factory=job_session_factory,
        lock_path=lock_path,
    )

    failed = await coordinator.run_daily(trigger="test", target_date=date(2026, 8, 18))
    assert failed == {"accepted": True, "success": False, "error": error}
    assert not lock_path.exists()

    fail = False
    completed = await coordinator.run_daily(trigger="test", target_date=date(2026, 8, 18))
    assert completed["accepted"] is True
    assert completed["success"] is True
    assert not lock_path.exists()

    with job_session_factory() as db:
        failed_job = db.scalar(select(Job).where(Job.status == "failed"))
        completed_job = db.scalar(select(Job).where(Job.status == "completed"))
        assert failed_job is not None
        assert completed_job is not None
        assert failed_job.error == error
        assert completed_job.error is None


@pytest.mark.asyncio
async def test_concurrent_or_locked_job_raises_conflict(
    job_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "pipeline.lock"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return {"fetch_date": "2026-08-18", "fetched": 1}

    async def analyze_stage(*, job_id: str | None, report_date: date | None) -> dict[str, Any]:
        return {"screened": 1}

    async def finalize_stage(
        report_date: date, result: dict[str, Any], job_id: str | None
    ) -> dict[str, Any]:
        return result

    coordinator = PipelineCoordinator(
        initialize=lambda: None,
        sync_stage=sync_stage,
        analyze_stage=analyze_stage,
        finalize_daily_stage=finalize_stage,
        session_factory=job_session_factory,
        lock_path=lock_path,
    )

    lock_path.write_text("external-owner\n", encoding="utf-8")
    with pytest.raises(JobConflict, match="已有抓取或分析任务正在运行"):
        coordinator.start_job("sync")
    lock_path.unlink()

    running = asyncio.create_task(
        coordinator.run_daily(trigger="test", target_date=date(2026, 8, 18))
    )
    await entered.wait()
    with pytest.raises(JobConflict, match="已有抓取或分析任务正在运行"):
        coordinator.start_job("sync")
    release.set()

    assert (await running)["success"] is True
    assert not lock_path.exists()
