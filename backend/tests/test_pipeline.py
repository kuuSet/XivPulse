from __future__ import annotations

from datetime import date
from typing import Any

import app.pipeline as pipeline_module
import pytest
from app.pipeline import PipelineCoordinator


@pytest.mark.asyncio
async def test_daily_pipeline_runs_stages_in_order_and_merges_results() -> None:
    calls: list[tuple[str, object]] = []

    async def sync_stage() -> dict[str, Any]:
        calls.append(("sync", None))
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

    result = await coordinator.execute("daily", job_id=None)

    assert result == {
        "fetch_date": "2026-08-14",
        "fetched": 3,
        "screened": 2,
        "report_date": "2026-08-14",
    }
    assert calls == [
        ("sync", None),
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

    async def unexpected_sync() -> dict[str, Any]:
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
