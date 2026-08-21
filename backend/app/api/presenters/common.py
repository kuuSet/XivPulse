"""Shared presenter normalization helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from ...models import Analysis, Paper


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _latest_analysis(
    paper: Paper,
    kind: str | None = None,
    *,
    profile_id: int | None = None,
) -> Analysis | None:
    analyses = [
        item
        for item in paper.analyses
        if item.status == "completed"
        and (kind is None or item.kind == kind)
        and (profile_id is None or item.profile_id == profile_id)
    ]
    return max(
        analyses,
        key=lambda item: _as_utc(item.created_at) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )
