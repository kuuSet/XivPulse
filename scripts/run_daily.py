"""Run the daily pipeline once without requiring the web server."""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from app.workflow import daily_pipeline

    result = await daily_pipeline.run_daily(trigger="scheduled-task")
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
