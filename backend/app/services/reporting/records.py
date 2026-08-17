
"""Deterministic direction trends, research maps, and daily research reports.

The analytics in this module deliberately stay local and model-free.  They
summarize the data already stored in ``Paper``, ``PaperMatch`` and ``Analysis``
and return JSON-ready dictionaries that can be exposed by an API route.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from itertools import combinations
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...models import Analysis, Digest, Paper, PaperMatch, Profile, Topic
from ..directions.matching import canonical_requested_direction_names, direction_evidence
from ..directions.taxonomy import (
    direction_taxonomy_fingerprint,
    has_current_direction_attribution,
)
from ..methods.catalog import match_method_labels
from ..organizations import paper_organizations

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_TREND_LIMIT = 12
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
class _Label:
    key: str
    name: str
    source: Literal["profile_topic"]
    terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PaperSignal:
    paper: Paper
    match: PaperMatch
    analysis: Analysis | None
    timestamp: datetime
    labels: tuple[_Label, ...]


def _as_timezone(timezone_name: str) -> tzinfo:
    if timezone_name.casefold() in {"utc", "etc/utc", "z"}:
        return UTC
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        # Windows does not ship an IANA timezone database.  Shanghai has had no
        # daylight-saving changes since 1991, so UTC+8 is correct for this app's
        # contemporary arXiv dates even when the optional ``tzdata`` wheel is absent.
        if timezone_name.casefold() in {"asia/shanghai", "prc"}:
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc


def _target_day(value: date | datetime | None, timezone: tzinfo) -> date:
    if value is None:
        return datetime.now(timezone).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(timezone).date()
    return value


def current_date_in_timezone(timezone_name: str = DEFAULT_TIMEZONE) -> date:
    """Return the current calendar date in the configured research timezone."""

    return _target_day(None, _as_timezone(timezone_name))


def _day_bounds(day: date, timezone: tzinfo) -> tuple[datetime, datetime]:
    local_start = datetime.combine(day, time.min, tzinfo=timezone)
    return local_start.astimezone(UTC), (local_start + timedelta(days=1)).astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    number = _number(match.relevance_score)
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


def _topic_id(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"topic-{digest}"


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


def _labels(
    match: PaperMatch,
    *,
    topics_by_key: Mapping[str, _Label],
    topics_by_id: Mapping[int, _Label],
    configured_topics: Sequence[Topic],
    profile_id: int,
    taxonomy_fingerprint: str,
) -> tuple[_Label, ...]:

    """Return only current, enabled profile topics matched by this paper.

    ``matched_topics_json`` is a historical snapshot and can contain model-created
    phrases, special match reasons, or topics later removed from the profile.  A
    bare ``topic_id`` also has no evidence or taxonomy provenance.  The current
    taxonomy plus current keyword or stamped-screening evidence is therefore the
    authority for analytics labels.
    """

    result: list[_Label] = []
    seen: set[str] = set()
    candidates: list[_Label] = []

    searchable = f"{match.paper.title}\n{match.paper.abstract}"
    for topic in configured_topics:
        label = topics_by_id.get(topic.id)
        if label is not None and direction_evidence(searchable, topic).matched:
            candidates.append(label)

    screening = [
        analysis
        for analysis in match.paper.analyses
        if analysis.profile_id == profile_id
        and analysis.kind == "screening"
        and analysis.status == "completed"
    ]
    if screening:
        latest_screening = max(
            screening,
            key=lambda item: (_as_utc(item.created_at), item.id or 0),
        )
        screening_payload = _payload(latest_screening)
        if has_current_direction_attribution(screening_payload, taxonomy_fingerprint):
            payload_topics = canonical_requested_direction_names(
                _payload_list(screening_payload, "matched_topics"),
                configured_topics,
                relevant=screening_payload.get("relevant") is True,
            )
            for raw in payload_topics:
                matched_topic = topics_by_key.get(_key(_clean_phrase(raw, max_length=120)))
                if matched_topic is not None:
                    candidates.append(matched_topic)
    for label in candidates:
        if label.key in seen:
            continue
        seen.add(label.key)
        result.append(label)
    return tuple(result)


def _load_records(session: Session, profile_id: int) -> list[_PaperSignal]:
    profile = session.scalar(
        select(Profile)
        .where(Profile.id == profile_id)
        .options(selectinload(Profile.topics))
        .execution_options(populate_existing=True)
    )
    configured_topics = profile.topics if profile is not None else []
    taxonomy_fingerprint = direction_taxonomy_fingerprint(configured_topics)
    topics_by_key: dict[str, _Label] = {}
    topics_by_id: dict[int, _Label] = {}
    for topic in configured_topics:
        if not topic.enabled:
            continue
        name = _clean_phrase(topic.name, max_length=120)
        normalized = _key(name)
        if not name or not normalized:
            continue
        label = topics_by_key.setdefault(
            normalized,
            _Label(normalized, name, "profile_topic", tuple(topic.keywords)),
        )
        if topic.id is not None:
            topics_by_id[topic.id] = label

    matches = session.scalars(
        select(PaperMatch)
        .where(PaperMatch.profile_id == profile_id)
        .options(selectinload(PaperMatch.paper).selectinload(Paper.analyses))
        .execution_options(populate_existing=True)
        .order_by(PaperMatch.paper_id)
    ).all()
    records: list[_PaperSignal] = []
    for match in matches:
        if match.paper.withdrawn:
            continue
        analysis = _latest_analysis(match.paper, profile_id)
        records.append(
            _PaperSignal(
                paper=match.paper,
                match=match,
                analysis=analysis,
                timestamp=_paper_time(match.paper),
                labels=_labels(
                    match,
                    topics_by_key=topics_by_key,
                    topics_by_id=topics_by_id,
                    configured_topics=[topic for topic in configured_topics if topic.enabled],
                    profile_id=profile_id,
                    taxonomy_fingerprint=taxonomy_fingerprint,
                ),
            )
        )
    return records


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


def _representative_papers(
    records: Iterable[_PaperSignal], *, limit: int = 3
) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": record.paper.id,
            "arxiv_id": record.paper.arxiv_id,
            "title": record.paper.title,
            "score": _score(record.match),
        }
        for record in _sort_records(records)[:limit]
    ]


def _period_payload(
    current: Sequence[_PaperSignal],
    previous: Sequence[_PaperSignal],
    *,
    granularity: Literal["day", "week"],
    current_start: date,
    current_end: date,
    previous_start: date,
    previous_end: date,
    limit: int,
) -> dict[str, Any]:
    candidate_count = len(current)
    previous_candidate_count = len(previous)
    current = [record for record in current if record.labels]
    previous = [record for record in previous if record.labels]
    current_counts: Counter[str] = Counter()
    previous_counts: Counter[str] = Counter()
    labels: dict[str, _Label] = {}
    sources: defaultdict[str, set[str]] = defaultdict(set)
    current_records: defaultdict[str, list[_PaperSignal]] = defaultdict(list)

    for record in current:
        for label in record.labels:
            current_counts[label.key] += 1
            current_records[label.key].append(record)
            labels.setdefault(label.key, label)
            sources[label.key].add(label.source)
    for record in previous:
        for label in record.labels:
            previous_counts[label.key] += 1
            labels.setdefault(label.key, label)
            sources[label.key].add(label.source)

    keys = sorted(
        current_counts.keys() | previous_counts.keys(),
        key=lambda value: (
            -current_counts[value],
            -(current_counts[value] - previous_counts[value]),
            labels[value].name.casefold(),
        ),
    )[: max(0, limit)]
    topics: list[dict[str, Any]] = []
    for value in keys:
        count = current_counts[value]
        previous_count = previous_counts[value]
        change = count - previous_count
        if count > 0 and previous_count == 0:
            direction = "new"
        elif change > 0:
            direction = "rising"
        elif change < 0:
            direction = "falling"
        else:
            direction = "stable"
        records_for_topic = current_records[value]
        topics.append(
            {
                "id": _topic_id(value),
                "name": labels[value].name,
                "sources": sorted(sources[value]),
                "count": count,
                "previous_count": previous_count,
                "change": change,
                "change_percent": (
                    round(change / previous_count * 100.0, 1) if previous_count else None
                ),
                "direction": direction,
                "share_percent": round(count / len(current) * 100.0, 1) if current else 0.0,
                "average_score": _mean(_score(item.match) for item in records_for_topic),
                "average_quality": _mean(_quality(item.analysis) for item in records_for_topic),
                "representative_papers": _representative_papers(records_for_topic),
            }
        )
    return {
        "granularity": granularity,
        "start_date": current_start.isoformat(),
        "end_date": current_end.isoformat(),
        "previous_start_date": previous_start.isoformat(),
        "previous_end_date": previous_end.isoformat(),
        "paper_count": len(current),
        "previous_paper_count": len(previous),
        "candidate_paper_count": candidate_count,
        "previous_candidate_paper_count": previous_candidate_count,

        "direction_attributed_paper_count": len(current),
        "previous_direction_attributed_paper_count": len(previous),
        "relevant_paper_count": len(current),
        "previous_relevant_paper_count": len(previous),
        "topics": topics,
    }
