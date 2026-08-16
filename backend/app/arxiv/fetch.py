"""Fetch one complete arXiv submission day."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import date

import arxiv

from ..domain.paper import Paper
from .results import to_papers

_PAGE_SIZE = 200
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")


class ArxivFetchError(RuntimeError):
    """Raised when arXiv cannot provide a complete, trustworthy result."""


async def fetch_daily(
    categories: Sequence[str],
    *,
    target_date: date,
    limit: int,
    client: arxiv.Client | None = None,
    rate_limit_seconds: float = 3.0,
) -> list[Paper]:
    clean = list(dict.fromkeys(value.strip() for value in categories))
    if not clean:
        raise ValueError("At least one valid arXiv category is required")
    invalid = [value for value in clean if not _CATEGORY_RE.fullmatch(value)]
    if invalid:
        raise ValueError("Invalid arXiv categories: " + ", ".join(invalid))
    if limit < 1:
        raise ValueError("arXiv result limit must be positive")

    compact_date = target_date.strftime("%Y%m%d")
    category_query = " OR ".join(f"cat:{category}" for category in clean)
    search = arxiv.Search(
        query=(
            f"({category_query}) AND "
            f"submittedDate:[{compact_date}0000 TO {compact_date}2359]"
        ),
        max_results=limit + 1,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Ascending,
    )
    metadata = client
    if metadata is None:
        metadata = arxiv.Client(
            page_size=_PAGE_SIZE,
            delay_seconds=max(0.0, rate_limit_seconds),
            num_retries=2,
        )
    try:
        results = await asyncio.to_thread(lambda: list(metadata.results(search)))
    except Exception as exc:
        raise ArxivFetchError("arXiv metadata request failed") from exc
    if len(results) > limit:
        raise ArxivFetchError(
            f"arXiv results exceed the configured limit {limit} for {target_date}"
        )
    try:
        return to_papers(results, target_date)
    except ValueError as exc:
        raise ArxivFetchError(str(exc)) from exc
