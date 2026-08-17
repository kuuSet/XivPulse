"""Application workflow composition shared by process entrypoints."""

import asyncio

from .pipeline import PipelineCoordinator
from .runtime import initialize_application
from .services.jobs import update_job_progress
from .services.paper_analysis import analyze_papers
from .services.paper_sync import sync_papers
from .services.reporting.service import finalize_daily_stage
from .services.semantic import get_semantic_services


async def rescore_semantic(*, force: bool, job_id: str | None) -> dict[str, object]:
    def report_progress(processed: int, total: int) -> None:
        ratio = processed / total if total else 1.0
        update_job_progress(
            job_id,
            progress=5 + round(ratio * 8),
            message=f"正在重算历史语义分数 {processed}/{total}",
        )

    result = await asyncio.to_thread(
        get_semantic_services().rescorer.rescore,
        force=force,
        progress=report_progress,
    )
    if result["status"] == "pending":
        raise RuntimeError("研究方向在重算期间发生变化，请重新运行")
    return {"semantic_rescore": result}


daily_pipeline = PipelineCoordinator(
    initialize=initialize_application,
    semantic_rescore_stage=rescore_semantic,
    sync_stage=sync_papers,
    analyze_stage=analyze_papers,
    finalize_daily_stage=finalize_daily_stage,
)
