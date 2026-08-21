"""Run the daily pipeline once without requiring the web server."""

from __future__ import annotations

import asyncio

from app.modules.jobs.command import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
