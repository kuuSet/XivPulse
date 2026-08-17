from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import app.pipeline as pipeline_module
import pytest
from app.pipeline import PipelineCoordinator


def test_pipeline_lock_is_released_only_when_the_guarded_work_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "pipeline.lock"
    monkeypatch.setattr(pipeline_module, "PIPELINE_LOCK_PATH", lock_path)

    assert pipeline_module._acquire_pipeline_lock("failed-job")
    with pytest.raises(RuntimeError, match="boom"):
        with pipeline_module._release_lock_on_failure("failed-job"):
            raise RuntimeError("boom")
    assert not lock_path.exists()

    assert pipeline_module._acquire_pipeline_lock("successful-job")
    with pipeline_module._release_lock_on_failure("successful-job"):
        pass
    assert lock_path.exists()
    pipeline_module._release_pipeline_lock("successful-job")


@pytest.mark.asyncio
async def test_daily_pipeline_runs_stages_in_order_and_merges_results() -> None:
    calls: list[tuple[str, object]] = []

    async def sync_stage(*, target_date: date | None) -> dict[str, Any]:
        calls.append(("sync", target_date))
        return {"fetch_date": "2026-08-14", "fetched": 3}

    async def analyze_stage(
        *, job_id: str | None, report_date: date | None
    ) -> dict[str, Any]:
        calls.append(("analyze", (job_id, report_date)))
        return {"screened": 2}

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
    )

    result = await coordinator.execute(
        "daily", job_id=None, target_date=date(2026, 8, 14)
    )

    assert result == {
        "fetch_date": "2026-08-14",
        "fetched": 3,
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
                {"fetch_date": "2026-08-14", "fetched": 3, "screened": 2},
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_analyze_pipeline_skips_sync_and_daily_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline_module, "update_job_progress", lambda *args, **kwargs: None)

    async def unexpected_sync(*, target_date: date | None) -> dict[str, Any]:
        raise AssertionError("sync stage should not run")

    async def analyze_stage(
        *, job_id: str | None, report_date: date | None
    ) -> dict[str, Any]:
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
    )

    assert await coordinator.execute("analyze", job_id="job-1") == {"analyzed": 1}


@pytest.mark.asyncio
async def test_windows_runner_uses_shared_daily_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import workflow

    from scripts import run_daily

    async def run_daily_once(*, trigger: str) -> dict[str, Any]:
        assert trigger == "scheduled-task"
        return {"accepted": True, "success": True, "fetched": 3, "analyzed": 2}

    monkeypatch.setattr(workflow.daily_pipeline, "run_daily", run_daily_once)

    assert await run_daily.main() == 0
