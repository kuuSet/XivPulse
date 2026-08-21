"""Shared presenter normalization helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)
