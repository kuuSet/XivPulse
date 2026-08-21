"""Shared records and values for daily research reports."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...models import Analysis, Paper, PaperMatch

DEFAULT_TIMEZONE = "Asia/Shanghai"
DAILY_QUALITY_SCORE_THRESHOLD = 80.0
DAILY_QUALITY_OVERVIEW_LIMIT = 6
ARXIV_BATCH_DATE_BASIS = (
    "arXiv submittedDate 的投稿时间（published_at；缺失时回退 first_seen_at），"
    "按设置时区归入报告日期"
)

_EVIDENCE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "真实机器人实验",
        ("real-world robot", "real robot", "physical robot", "真实机器人", "真机实验", "实机实验"),
    ),
    (
        "仿真实验",
        ("simulation experiment", "simulated environment", "in simulation", "模拟器", "仿真实验"),
    ),
    ("基准评测", ("benchmark", "state-of-the-art", "sota", "基准评测", "基准实验")),
    ("消融实验", ("ablation", "ablative study", "消融实验", "消融研究")),
    ("多任务评测", ("multi-task", "multitask", "multiple tasks", "多任务评测", "多类任务")),
    (
        "跨环境泛化",
        ("generalization", "unseen environment", "out-of-distribution", "跨环境泛化", "未见环境"),
    ),
    ("数据集", ("dataset", "corpus", "数据集", "语料库")),
    ("理论分析", ("theoretical analysis", "theorem", "proof", "理论分析", "定理证明")),
    ("用户研究", ("user study", "human evaluation", "用户研究", "人类评测")),
)


@dataclass(frozen=True, slots=True)
class _PaperSignal:
    paper: Paper
    match: PaperMatch
    analysis: Analysis | None
    timestamp: datetime


def _as_timezone(timezone_name: str) -> tzinfo:
    return ZoneInfo(timezone_name)


def _target_day(value: date | datetime | None, timezone: tzinfo) -> date:
    if value is None:
        return datetime.now(timezone).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(timezone).date()
    return value


def current_date_in_timezone(timezone_name: str = DEFAULT_TIMEZONE) -> date:
    return _target_day(None, _as_timezone(timezone_name))


def _day_bounds(day: date, timezone: tzinfo) -> tuple[datetime, datetime]:
    local_start = datetime.combine(day, time.min, tzinfo=timezone)
    return local_start.astimezone(UTC), (local_start + timedelta(days=1)).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _paper_time(paper: Paper) -> datetime:
    return _as_utc(paper.published_at or paper.first_seen_at)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _mean(values: Iterable[float | None]) -> float | None:
    numbers = [value for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 1) if numbers else None


def _score(match: PaperMatch) -> float:
    number = _number(match.effective_score)
    return round(max(0.0, min(100.0, number)), 1) if number is not None else 0.0


def _quality(analysis: Analysis | None) -> float | None:
    if analysis is None:
        return None
    return _mean(
        (
            _number(analysis.novelty_score),
            _number(analysis.rigor_score),
            _number(analysis.impact_score),
        )
    )


def _clean_phrase(value: object, *, max_length: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    result = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip(" ,;；。")
    if len(result) <= max_length:
        return result
    return result[: max_length - 1].rstrip() + "…"


def _key(value: str) -> str:
    return _clean_phrase(value).casefold()


def _payload(analysis: Analysis | None) -> dict[str, Any]:
    return dict(analysis.payload_json or {}) if analysis is not None else {}


def _payload_list(payload: Mapping[str, Any], name: str) -> list[str]:
    raw = payload.get(name, [])
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_phrase(item)
        normalized = _key(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return result


def _latest_analysis(paper: Paper, profile_id: int) -> Analysis | None:
    candidates = [
        item
        for item in paper.analyses
        if item.profile_id == profile_id and item.status == "completed"
    ]
    if not candidates:
        return None
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return max(
        candidates,
        key=lambda item: (
            item.kind == "deep",
            (_as_utc(item.created_at) - epoch).total_seconds(),
            item.id or 0,
        ),
    )


def _load_records(session: Session, profile_id: int) -> list[_PaperSignal]:
    matches = session.scalars(
        select(PaperMatch)
        .where(PaperMatch.profile_id == profile_id, PaperMatch.screened_at.is_not(None))
        .options(selectinload(PaperMatch.paper).selectinload(Paper.analyses))
        .execution_options(populate_existing=True)
        .order_by(PaperMatch.paper_id)
    ).all()
    return [
        _PaperSignal(
            paper=match.paper,
            match=match,
            analysis=_latest_analysis(match.paper, profile_id),
            timestamp=_paper_time(match.paper),
        )
        for match in matches
        if not match.paper.withdrawn
    ]


def _within(records: Iterable[_PaperSignal], start: datetime, end: datetime) -> list[_PaperSignal]:
    return [record for record in records if start <= record.timestamp < end]


def _sort_records(records: Iterable[_PaperSignal]) -> list[_PaperSignal]:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return sorted(
        records,
        key=lambda record: (
            -_score(record.match),
            -(record.timestamp - epoch).total_seconds(),
            record.paper.arxiv_id,
            record.paper.id,
        ),
    )
