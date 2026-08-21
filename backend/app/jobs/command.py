"""Run the daily pipeline once from a command-line entrypoint."""

from __future__ import annotations

import sys

from ..bootstrap import bootstrap_application
from .service import PipelineCoordinator
from .workflow import build_pipeline


async def main(pipeline: PipelineCoordinator | None = None) -> int:
    if pipeline is None:
        bootstrap_application()
        pipeline = build_pipeline()
    result = await pipeline.run_daily(trigger="scheduled-task")
    if not result.get("accepted", False):
        print("A daily job is already running.", file=sys.stderr)
        return 2
    if not result.get("success", False):
        print("The daily job failed. Open the web app for status details.", file=sys.stderr)
        return 1
    print(
        "Daily update complete: "
        f"fetched={result.get('fetched', 0)}, "
        f"analyzed={result.get('analyzed', 0)}"
    )
    return 0
