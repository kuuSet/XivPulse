"""Shared presenter normalization helpers."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

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
        if (kind is None or item.kind == kind)
        and (profile_id is None or item.profile_id == profile_id)
    ]
    return max(
        analyses,
        key=lambda item: _as_utc(item.created_at) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )


def _direction_key(value: object) -> str:
    """Normalize exact names and aliases without semantic fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def _canonical_directions(values: Any, names_by_key: dict[str, str]) -> list[str]:
    if not isinstance(values, list | tuple | set):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        key = _direction_key(value)
        canonical = names_by_key.get(key)
        if canonical is not None and key not in seen:
            seen.add(key)
            result.append(canonical)
    return result


def _clean_tags(value: Any, *, limit: int = 50) -> list[str]:
    """Keep the first spelling while collapsing visually equivalent user tags."""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = unicodedata.normalize("NFKC", item).strip()
        key = re.sub(r"[\s_-]+", "", cleaned).casefold()
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
