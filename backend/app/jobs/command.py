"""Run the daily pipeline once from a command-line entrypoint."""

from __future__ import annotations

import sys

from .service import PipelineCoordinator
from .workflow import daily_pipeline


async def main(pipeline: PipelineCoordinator = daily_pipeline) -> int:
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
