"""Persisted analysis queries for method candidate discovery."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ...models import Analysis, Paper, utcnow
from .catalog import (
    METHOD_CANDIDATE_MIN_PAPERS,
    METHOD_CANDIDATE_WINDOW_DAYS,
    discover_method_candidates,
)
from .types import CustomMethod


def discover_saved_method_candidates(
    db: Session,
    *,
    custom_values: Iterable[CustomMethod] = (),
    dismissed: Iterable[str] = (),
) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=METHOD_CANDIDATE_WINDOW_DAYS)
    rows = db.execute(
        select(Analysis, Paper)
        .join(Paper, Paper.id == Analysis.paper_id)
        .where(Analysis.status == "completed", Analysis.created_at >= cutoff)
        .order_by(desc(Analysis.created_at))
    ).all()
    records: list[dict[str, Any]] = []
    seen: set[int] = set()
    # Screening usually has no methods, so take the newest method-bearing analysis per paper.
    for analysis, paper in rows:
        methods = analysis.payload.get("methods", [])
        if paper.id in seen or not isinstance(methods, list) or not methods:
            continue
        seen.add(paper.id)
        records.append(
            {
                "paper_id": paper.id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "methods": methods,
            }
        )
    return discover_method_candidates(
        records,
        custom_values=custom_values,
        dismissed=dismissed,
        min_papers=METHOD_CANDIDATE_MIN_PAPERS,
    )


__all__ = ["discover_saved_method_candidates"]
