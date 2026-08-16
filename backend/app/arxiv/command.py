"""Fetch one arXiv submission day without running the application pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date

from .client import ArxivClient


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.arxiv.command",
        description=__doc__,
    )
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--category", required=True, action="append", dest="categories")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)

    papers = asyncio.run(
        ArxivClient().fetch_daily(
            args.categories,
            target_date=args.date,
            max_results=args.limit,
        )
    )
    for paper in papers:
        print(json.dumps(paper.as_dict(), ensure_ascii=False))
    print(f"Fetched {len(papers)} papers for {args.date.isoformat()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
