"""Research-direction trend reports."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Digest
from .records import (
    ARXIV_BATCH_DATE_BASIS,
    DEFAULT_TIMEZONE,
    DEFAULT_TREND_LIMIT,
    _as_timezone,
    _day_bounds,
    _load_records,
    _period_payload,
    _target_day,
    _within,
)


def build_topic_trends(
    session: Session,
    profile_id: int,
    *,
    as_of: date | datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    limit: int = DEFAULT_TREND_LIMIT,
) -> dict[str, Any]:
    """Return daily and rolling seven-day research-direction trends for one profile.

    The current day is compared with the previous calendar day.  The current
    seven-day window (ending on ``as_of``) is compared with the preceding seven
    days.  Topic counts are paper counts, not raw tag mention counts.
    """

    timezone = _as_timezone(timezone_name)
    requested_day = _target_day(as_of, timezone)
    day = requested_day
    awaiting_daily_update = False
    if as_of is None:
        latest_complete_day = session.scalar(
            select(Digest.digest_date)
            .where(Digest.profile_id == profile_id, Digest.digest_date <= requested_day)
            .order_by(Digest.digest_date.desc())
            .limit(1)
        )
        if latest_complete_day is not None and latest_complete_day < requested_day:
            day = latest_complete_day
            awaiting_daily_update = True
    day_start, day_end = _day_bounds(day, timezone)
    previous_day_start, _ = _day_bounds(day - timedelta(days=1), timezone)
    week_start, _ = _day_bounds(day - timedelta(days=6), timezone)
    previous_week_start, _ = _day_bounds(day - timedelta(days=13), timezone)
    records = _load_records(session, profile_id)

    daily = _period_payload(
        _within(records, day_start, day_end),
        _within(records, previous_day_start, day_start),
        granularity="day",
        current_start=day,
        current_end=day,
        previous_start=day - timedelta(days=1),
        previous_end=day - timedelta(days=1),
        limit=limit,
    )
    weekly = _period_payload(
        _within(records, week_start, day_end),
        _within(records, previous_week_start, week_start),
        granularity="week",
        current_start=day - timedelta(days=6),
        current_end=day,
        previous_start=day - timedelta(days=13),
        previous_end=day - timedelta(days=7),
        limit=limit,
    )
    return {
        "profile_id": profile_id,
        "as_of": day.isoformat(),
        "requested_as_of": requested_day.isoformat(),
        "latest_complete_date": day.isoformat(),
        "awaiting_daily_update": awaiting_daily_update,
        "timezone": timezone_name,
        "date_basis": ARXIV_BATCH_DATE_BASIS,
        "daily": daily,
        "weekly": weekly,
    }
