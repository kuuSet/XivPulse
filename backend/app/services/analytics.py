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

from ..models import Analysis, Digest, Paper, PaperMatch, Profile, Topic
from .direction_matching import canonical_requested_direction_names, direction_evidence
from .direction_taxonomy import (
    direction_taxonomy_fingerprint,
    has_current_direction_attribution,
)
from .method_catalog import match_method_labels
from .organizations import paper_organizations

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


def _has_term(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9+.-]+", term, re.I):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text, re.I) is not None
    return term.casefold() in text.casefold()


def _controlled_terms(text: str, vocabulary: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    return [label for label, terms in vocabulary if any(_has_term(text, term) for term in terms)]


def _method_labels(
    record: _PaperSignal,
    custom_terms: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return stable, controlled labels for cross-paper aggregation."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "methods")
    text = "\n".join(
        (
            record.paper.title,
            record.paper.abstract,
            " ".join(record.paper.categories),
            record.analysis.summary if record.analysis is not None else "",
            " ".join(explicit),
        )
    )
    return match_method_labels(text, custom_terms)


def _evidence_labels(record: _PaperSignal) -> list[str]:
    """Return evidence types, never paper-specific evidence statements."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "evidence")
    text = "\n".join(
        (
            record.paper.title,
            record.paper.abstract,
            record.analysis.summary if record.analysis is not None else "",
            " ".join(explicit),
        )
    )
    return _controlled_terms(text, _EVIDENCE_TERMS)


def _method_items(record: _PaperSignal) -> list[str]:
    """Return paper-level method details for the paper highlight."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "methods")
    return _unique_phrases([*explicit, *_method_labels(record)])


def _evidence_items(record: _PaperSignal) -> list[str]:
    """Return paper-level evidence details for the paper highlight."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "evidence")
    return _unique_phrases([*explicit, *_evidence_labels(record)])


def _unique_phrases(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _clean_phrase(raw)
        normalized = _key(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return result


def build_research_roadmap(
    session: Session,
    profile_id: int,
    *,
    as_of: date | datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    days: int = 90,
    max_nodes: int = 24,
    max_relations: int = 60,
) -> dict[str, Any]:
    """Build a research-direction co-occurrence map and weekly timeline.

    Relations mean that two configured directions appeared on the same paper.  They are not
    citation or causal claims, which cannot be established from the local data.
    """

    if days < 1:
        raise ValueError("days must be at least 1")
    timezone = _as_timezone(timezone_name)
    day = _target_day(as_of, timezone)
    window_start_day = day - timedelta(days=days - 1)
    window_start, _ = _day_bounds(window_start_day, timezone)
    _, window_end = _day_bounds(day, timezone)
    candidate_records = _within(_load_records(session, profile_id), window_start, window_end)
    records = [record for record in candidate_records if record.labels]

    by_label: defaultdict[str, list[_PaperSignal]] = defaultdict(list)
    label_by_key: dict[str, _Label] = {}
    source_by_key: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        for label in record.labels:
            by_label[label.key].append(record)
            label_by_key.setdefault(label.key, label)
            source_by_key[label.key].add(label.source)

    selected_keys = sorted(
        by_label,
        key=lambda value: (
            -len(by_label[value]),
            -(_mean(_score(item.match) for item in by_label[value]) or 0.0),
            label_by_key[value].name.casefold(),
        ),
    )[: max(0, max_nodes)]
    selected = set(selected_keys)
    recent_cutoff = day - timedelta(days=min(13, days - 1))
    nodes: list[dict[str, Any]] = []
    for value in selected_keys:
        topic_records = by_label[value]
        dates = [item.timestamp.astimezone(timezone).date() for item in topic_records]
        keyword_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        for item in topic_records:
            searchable = f"{item.paper.title}\n{item.paper.abstract}"
            keyword_counts.update(
                term for term in label_by_key[value].terms if _has_term(searchable, term)
            )
            method_counts.update(_method_labels(item))
        first_seen = min(dates)
        last_seen = max(dates)
        if first_seen >= recent_cutoff:
            status = "emerging"
        elif last_seen >= recent_cutoff:
            status = "active"
        else:
            status = "established"
        nodes.append(
            {
                "id": _topic_id(value),
                "label": label_by_key[value].name,
                "sources": sorted(source_by_key[value]),
                "paper_count": len(topic_records),
                "average_score": _mean(_score(item.match) for item in topic_records),
                "average_quality": _mean(_quality(item.analysis) for item in topic_records),
                "first_seen": first_seen.isoformat(),
                "last_seen": last_seen.isoformat(),
                "status": status,
                "keywords": [name for name, _ in keyword_counts.most_common(6)],
                "methods": [name for name, _ in method_counts.most_common(6)],
                "representative_papers": _representative_papers(topic_records),
            }
        )

    relation_papers: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    for record in records:
        values = sorted({label.key for label in record.labels if label.key in selected})
        for left, right in combinations(values, 2):
            relation_papers[(left, right)].add(record.paper.id)
    relations: list[dict[str, Any]] = []
    for (left, right), paper_ids in sorted(
        relation_papers.items(),
        key=lambda item: (
            -len(item[1]),
            label_by_key[item[0][0]].name.casefold(),
            label_by_key[item[0][1]].name.casefold(),
        ),
    )[: max(0, max_relations)]:
        denominator = min(len(by_label[left]), len(by_label[right]))
        relations.append(
            {
                "source": _topic_id(left),
                "target": _topic_id(right),
                "type": "co_occurrence",
                "directed": False,
                "weight": len(paper_ids),
                "strength_percent": round(len(paper_ids) / denominator * 100.0, 1),
                "paper_ids": sorted(paper_ids),
            }
        )

    weekly: defaultdict[date, list[_PaperSignal]] = defaultdict(list)
    for record in records:
        local_day = record.timestamp.astimezone(timezone).date()
        weekly[local_day - timedelta(days=local_day.weekday())].append(record)
    timeline: list[dict[str, Any]] = []
    for week_start in sorted(weekly):
        week_records = weekly[week_start]
        active_keys = {
            label.key for record in week_records for label in record.labels if label.key in selected
        }
        timeline.append(
            {
                "start_date": week_start.isoformat(),
                "end_date": min(week_start + timedelta(days=6), day).isoformat(),
                "paper_count": len(week_records),
                "topic_ids": [_topic_id(value) for value in selected_keys if value in active_keys],
                "top_papers": _representative_papers(week_records, limit=2),
            }
        )

    return {
        "profile_id": profile_id,
        "as_of": day.isoformat(),
        "timezone": timezone_name,
        "window": {
            "start_date": window_start_day.isoformat(),
            "end_date": day.isoformat(),
            "days": days,
        },
        "coverage": {
            "paper_count": len(records),
            "candidate_paper_count": len(candidate_records),
            "direction_attributed_paper_count": len(records),
            "relevant_paper_count": len(records),
            "analyzed_paper_count": sum(record.analysis is not None for record in records),
            "node_count": len(nodes),
            "relation_count": len(relations),
        },
        "nodes": nodes,
        "relations": relations,
        "timeline": timeline,
        "date_basis": ARXIV_BATCH_DATE_BASIS,
        "relation_basis": (
            "仅统计能够归入当前正式研究方向的论文；关系只表示两个方向在同篇论文中"
            "共现，不代表引用、因果或方法继承关系。"
        ),
    }


def _aggregate_items(
    records: Sequence[_PaperSignal], extractor: Callable[[_PaperSignal], Iterable[str]]
) -> list[dict[str, Any]]:
    papers_by_key: defaultdict[str, set[int]] = defaultdict(set)
    display: dict[str, str] = {}
    for record in records:
        for item in extractor(record):
            normalized = _key(item)
            if not normalized:
                continue
            display.setdefault(normalized, item)
            papers_by_key[normalized].add(record.paper.id)
    values = sorted(
        papers_by_key,
        key=lambda value: (-len(papers_by_key[value]), display[value].casefold()),
    )
    return [
        {
            "name": display[value],
            "count": len(papers_by_key[value]),
            "paper_ids": sorted(papers_by_key[value]),
            "shared": len(papers_by_key[value]) >= 2,
        }
        for value in values
    ]


def _limitation_items(record: _PaperSignal) -> list[tuple[str, str]]:
    payload = _payload(record.analysis)
    explicit: list[str] = []
    if record.analysis is not None:
        explicit.extend(record.analysis.weaknesses_json or [])
    explicit.extend(_payload_list(payload, "limitations"))
    follow_ups = [f"开放问题：{value}" for value in _payload_list(payload, "follow_up_questions")]
    result = [(value, "analysis") for value in _unique_phrases([*explicit, *follow_ups])]
    if result:
        return result
    if not _evidence_items(record):
        return [
            (
                "摘要与现有分析未提供明确的实验或理论证据，需要阅读全文核查评估协议。",
                "coverage_check",
            )
        ]
    return [
        (
            "现有分析未明确记录局限，需要阅读全文核查泛化边界、失败案例与复现条件。",
            "coverage_check",
        )
    ]


def _aggregate_limitations(records: Sequence[_PaperSignal]) -> list[dict[str, Any]]:
    papers: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    display: dict[tuple[str, str], str] = {}
    for record in records:
        for text, basis in _limitation_items(record):
            item_key = (_key(text), basis)
            display.setdefault(item_key, text)
            papers[item_key].add(record.paper.id)
    keys = sorted(
        papers,
        key=lambda value: (-len(papers[value]), display[value].casefold(), value[1]),
    )
    return [
        {
            "text": display[value],
            "count": len(papers[value]),
            "paper_ids": sorted(papers[value]),
            "basis": value[1],
        }
        for value in keys[:10]
    ]


def _recommendation(record: _PaperSignal) -> Literal["must_read", "read", "skim", "skip"]:
    value = _payload(record.analysis).get("recommendation")
    if value in {"must_read", "read", "skim", "skip"}:
        return value
    score = _score(record.match)
    if score >= 90:
        return "must_read"
    if score >= 80:
        return "read"
    if score >= 60:
        return "skim"
    return "skip"


def _summary(record: _PaperSignal) -> str:
    if record.analysis is not None and record.analysis.summary.strip():
        return _clean_phrase(record.analysis.summary, max_length=360)
    abstract = _clean_phrase(record.paper.abstract, max_length=360)
    return abstract or "当前记录暂无摘要或分析。"


def _highlight(record: _PaperSignal) -> dict[str, Any]:
    payload = _payload(record.analysis)
    topics = [label.name for label in record.labels]
    reason = ""
    if record.analysis is not None:
        reason = _clean_phrase(record.analysis.verdict, max_length=260)
    reason = reason or _clean_phrase(record.match.reason, max_length=260)
    if not reason:
        reason = f"匹配研究方向：{'、'.join(topics) if topics else '待归类'}。"
    return {
        "paper_id": record.paper.id,
        "arxiv_id": record.paper.arxiv_id,
        "title": record.paper.title,
        "authors": record.paper.authors,
        "notable_organizations": paper_organizations(record.paper),
        "published_at": record.timestamp.isoformat(),
        "score": _score(record.match),
        "quality_score": _quality(record.analysis),
        "topics": topics,
        "summary": _summary(record),
        "why_it_matters": reason,
        "recommendation": _recommendation(record),
        "contributions": _payload_list(payload, "key_contributions")[:5],
        "methods": _method_items(record)[:8],
        "evidence": _evidence_items(record)[:8],
        "analysis_available": record.analysis is not None,
    }


def _reading_queue(records: Sequence[_PaperSignal], limit: int) -> list[dict[str, Any]]:
    priority = {"must_read": 0, "read": 1, "skim": 2, "skip": 3}
    ordered = sorted(
        records,
        key=lambda record: (
            priority[_recommendation(record)],
            -_score(record.match),
            record.paper.arxiv_id,
        ),
    )[: max(0, limit)]
    result: list[dict[str, Any]] = []
    for rank, record in enumerate(ordered, start=1):
        recommendation = _recommendation(record)
        if recommendation == "must_read":
            next_action = "阅读全文，核查核心方法、主要实验与失败案例"
            minutes = 35
        elif recommendation == "read":
            next_action = "重点阅读方法与实验章节"
            minutes = 25
        elif recommendation == "skim":
            next_action = "先读摘要、图表与结论，再决定是否深入"
            minutes = 12
        else:
            next_action = "仅保留主题记录，暂不投入深读时间"
            minutes = 5
        result.append(
            {
                "rank": rank,
                "paper_id": record.paper.id,
                "arxiv_id": record.paper.arxiv_id,
                "title": record.paper.title,
                "priority": recommendation,
                "score": _score(record.match),
                "estimated_minutes": minutes,
                "next_action": next_action,
                "reason": _highlight(record)["why_it_matters"],
            }
        )
    return result


def _executive_summary(
    day: date,
    records: Sequence[_PaperSignal],
    daily_topics: Sequence[Mapping[str, Any]],
    *,
    candidate_count: int,
) -> str:
    if not records:
        if candidate_count:
            return (
                f"{day.isoformat()} 共抓取 {candidate_count} 篇候选论文，但没有论文能够归入"
                "当前正式研究方向，因此不据此生成研究趋势、代表论文或阅读建议。"
            )
        return (
            f"{day.isoformat()} 未发现符合当前研究画像的新论文。"
            "建议回看近一周高分候选，并确认当日抓取任务是否正常完成。"
        )
    analyzed = sum(record.analysis is not None for record in records)
    high_score = sum(_score(record.match) >= 80 for record in records)
    top_names = [str(item["name"]) for item in daily_topics if int(item["count"]) > 0][:3]
    rising = [
        str(item["name"])
        for item in daily_topics
        if item["direction"] in {"new", "rising"} and int(item["count"]) > 0
    ][:2]
    topic_text = "、".join(top_names) if top_names else "尚未稳定归类的研究方向"
    trend_text = f"；新增或上升方向为{'、'.join(rising)}" if rising else ""
    return (
        f"{day.isoformat()} 共纳入 {len(records)} 篇论文，其中 {high_score} 篇推荐分不低于 80，"
        f"{analyzed} 篇已有可用分析。研究注意力主要集中在{topic_text}{trend_text}。"
    )


def _topic_insights(topics: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for topic in topics:
        count = int(topic["count"])
        if count <= 0:
            continue
        change = int(topic["change"])
        if topic["direction"] == "new":
            comparison = "前一日未出现，属于当日新增信号"
        elif change > 0:
            comparison = f"较前一日增加 {change} 篇"
        elif change < 0:
            comparison = f"较前一日减少 {abs(change)} 篇"
        else:
            comparison = "与前一日持平"
        average = topic["average_score"]
        score_text = f"，平均推荐分 {average:.1f}" if isinstance(average, float) else ""
        result.append(
            {
                "topic_id": topic["id"],
                "topic": topic["name"],
                "paper_count": count,
                "direction": topic["direction"],
                "average_score": average,
                "insight": f"今日 {count} 篇，{comparison}{score_text}。",
                "representative_papers": topic["representative_papers"],
            }
        )
    return result[:8]


def _research_situation(
    day: date,
    records: Sequence[_PaperSignal],
    previous: Sequence[_PaperSignal],
    recent_baseline: Sequence[_PaperSignal],
    daily_topics: Sequence[Mapping[str, Any]],
    methods: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Describe the day's research-level state rather than repeating paper summaries."""

    current_count = len(records)
    previous_count = len(previous)
    baseline_days = 7
    baseline_average = round(len(recent_baseline) / baseline_days, 1)
    if current_count > baseline_average * 1.25 and current_count >= 3:
        volume_status = "above_baseline"
        volume_text = f"明显高于近 {baseline_days} 日均值 {baseline_average:.1f} 篇"
    elif current_count < baseline_average * 0.75:
        volume_status = "below_baseline"
        volume_text = f"低于近 {baseline_days} 日均值 {baseline_average:.1f} 篇"
    else:
        volume_status = "near_baseline"
        volume_text = f"接近近 {baseline_days} 日均值 {baseline_average:.1f} 篇"

    active_topics = [item for item in daily_topics if int(item.get("count", 0)) > 0]
    rising_topics = [
        str(item["name"]) for item in active_topics if item.get("direction") in {"new", "rising"}
    ][:4]
    falling_topics = [
        str(item["name"]) for item in daily_topics if item.get("direction") == "falling"
    ][:4]
    direction_total = sum(int(item.get("count", 0)) for item in active_topics)
    top_share = (
        round(int(active_topics[0]["count"]) / direction_total * 100.0, 1)
        if active_topics and direction_total
        else 0.0
    )
    concentration = "high" if top_share >= 55 else "medium" if top_share >= 35 else "distributed"

    strong_evidence = {
        "真实机器人实验",
        "消融实验",
        "跨环境泛化",
        "理论分析",
        "用户研究",
    }
    deep_records = [
        record
        for record in records
        if record.analysis is not None and record.analysis.kind == "deep"
    ]
    deep_evidence = _aggregate_items(deep_records, _evidence_labels)
    shared_strong_evidence = [
        str(item.get("name", ""))
        for item in deep_evidence
        if str(item.get("name", "")) in strong_evidence and int(item.get("count", 0)) >= 2
    ]
    strong_evidence_count = len(shared_strong_evidence)
    deep_count = len(deep_records)
    deep_coverage = deep_count / len(records) if records else 0.0
    if not records:
        evidence_status = "no_signal"
        evidence_text = "当日没有新论文，无法形成新的证据判断"
    elif deep_coverage < 0.5:
        evidence_status = "insufficient_analysis"
        evidence_text = "多数相关论文尚未完成深度分析，方法与证据判断仍不充分"
    elif deep_coverage >= 0.75 and strong_evidence_count >= 2:
        evidence_status = "strong"
        evidence_text = "多类较强证据均得到至少两篇论文支持，且深度分析覆盖充分"
    elif strong_evidence_count:
        evidence_status = "mixed"
        evidence_text = "已经出现跨论文的较强证据，但证据类型或覆盖范围仍有限"
    else:
        evidence_status = "preliminary"
        evidence_text = "证据主要停留在摘要、基准或仿真层面，结论仍偏初步"

    method_names = [str(item.get("name", "")) for item in methods[:3] if item.get("name")]
    direction_names = [str(item["name"]) for item in active_topics[:3]]
    if not records:
        synthesis = (
            f"{day.isoformat()} 没有形成新的研究方向信号；建议把它视为低活动日，"
            "继续观察近一周方向变化，而不是从空样本推断趋势。"
        )
    else:
        volume_direction = (
            "增加"
            if current_count > previous_count
            else "减少"
            if current_count < previous_count
            else "持平"
        )
        direction_text = "、".join(direction_names) if direction_names else "尚未归入关注方向的论文"
        synthesis = (
            f"当日共纳入 {current_count} 篇，较昨日"
            f"{volume_direction}"
            f" {abs(current_count - previous_count)} 篇，{volume_text}。"
            f"研究注意力主要落在{direction_text}；"
            f"共同方法以{'、'.join(method_names) if method_names else '尚未形成稳定共性'}为主。"
            f"{evidence_text}。"
        )
    return {
        "synthesis": synthesis,
        "volume": {
            "today": current_count,
            "previous_day": previous_count,
            "change": current_count - previous_count,
            "recent_daily_average": baseline_average,
            "status": volume_status,
            "assessment": volume_text,
        },
        "direction_dynamics": {
            "active": direction_names,
            "rising": rising_topics,
            "falling": falling_topics,
            "top_direction_share_percent": top_share,
            "concentration": concentration,
        },
        "method_signals": method_names,
        "evidence_maturity": {
            "status": evidence_status,
            "assessment": evidence_text,
            "strong_signal_count": strong_evidence_count,
            "shared_strong_signals": shared_strong_evidence,
            "deep_analysis_coverage_percent": round(deep_coverage * 100.0, 1),
        },
    }


def _first_sentence(value: str, *, max_length: int = 220) -> str:
    text = _clean_phrase(value, max_length=max_length)
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？.!?])\s+", text, maxsplit=1)
    return parts[0].strip()


def _paper_overview_item(record: _PaperSignal) -> dict[str, Any]:
    payload = _payload(record.analysis)
    contributions = _payload_list(payload, "key_contributions")
    contribution = _first_sentence(contributions[0]) if contributions else ""
    contribution = contribution or _first_sentence(_summary(record))
    controlled_methods = _method_labels(record)
    detailed_methods = _method_items(record)
    method = "、".join(controlled_methods[:3])
    if not method and detailed_methods:
        method = _first_sentence(detailed_methods[0], max_length=140)
    controlled_evidence = _evidence_labels(record)
    evidence = "、".join(controlled_evidence[:3])
    if not evidence:
        detailed_evidence = _evidence_items(record)
        evidence = (
            _first_sentence(detailed_evidence[0], max_length=160)
            if detailed_evidence
            else "现有记录未提取到明确证据形态"
        )
    limitation_items = _limitation_items(record)
    limitation = (
        _first_sentence(limitation_items[0][0], max_length=180)
        if limitation_items
        else "现有记录尚未提取到明确局限"
    )
    return {
        "paper_id": record.paper.id,
        "arxiv_id": record.paper.arxiv_id,
        "title": record.paper.title,
        "score": _score(record.match),
        "topics": [label.name for label in record.labels],
        "contribution": contribution,
        "method": method or "现有记录未提取到稳定方法标签",
        "evidence": evidence,
        "limitation": limitation,
        "analysis_kind": record.analysis.kind if record.analysis is not None else None,
    }


def _quality_overview(
    records: Sequence[_PaperSignal],
    *,
    score_threshold: float = DAILY_QUALITY_SCORE_THRESHOLD,
    limit: int = DAILY_QUALITY_OVERVIEW_LIMIT,
) -> dict[str, Any]:
    """Compare today's stronger papers using only locally stored evidence."""

    qualified = [
        record for record in _sort_records(records) if _score(record.match) >= score_threshold
    ]
    selected = qualified[: max(0, limit)]
    analyzed = [record for record in selected if record.analysis is not None]
    deep = [
        record
        for record in selected
        if record.analysis is not None and record.analysis.kind == "deep"
    ]
    if not selected:
        return {
            "score_threshold": score_threshold,
            "qualified_paper_count": 0,
            "included_paper_count": 0,
            "analyzed_paper_count": 0,
            "deep_analysis_count": 0,
            "lead": f"当日没有推荐分达到 {score_threshold:.0f} 的论文，暂不强行生成优质论文结论。",
            "main_threads": [],
            "paper_findings": [],
            "comparison": "可先查看研究方向动态和较低分候选，等待更多分析后再形成横向判断。",
            "evidence_conclusion": "没有足够的高分样本支持当日优质论文综述。",
        }

    topic_papers: defaultdict[str, list[_PaperSignal]] = defaultdict(list)
    for record in selected:
        for label in record.labels:
            topic_papers[label.name].append(record)
    ordered_topics = sorted(
        topic_papers,
        key=lambda name: (
            -len(topic_papers[name]),
            -(_mean(_score(item.match) for item in topic_papers[name]) or 0.0),
            name.casefold(),
        ),
    )
    main_threads = [
        {
            "name": name,
            "paper_count": len(topic_papers[name]),
            "average_score": _mean(_score(item.match) for item in topic_papers[name]),
            "paper_ids": [item.paper.id for item in topic_papers[name]],
        }
        for name in ordered_topics[:4]
    ]
    method_items = _aggregate_items(selected, _method_labels)
    shared_methods = [str(item["name"]) for item in method_items if bool(item.get("shared"))][:4]
    evidence_items = _aggregate_items(selected, _evidence_labels)
    shared_evidence = [str(item["name"]) for item in evidence_items if bool(item.get("shared"))][:4]
    thread_text = "、".join(item["name"] for item in main_threads[:3])
    lead = (
        f"当日 {len(records)} 篇正式方向论文中有 {len(qualified)} 篇达到"
        f"推荐分 {score_threshold:.0f}；本总览比较排名前 {len(selected)} 篇。"
        f"高分信号主要集中在{thread_text or '尚未形成稳定方向聚类'}，"
        f"其中 {len(analyzed)} 篇已有 AI 分析、{len(deep)} 篇完成全文深读。"
    )
    if len(main_threads) >= 2:
        comparison = (
            f"这些论文并非同一条路线：{main_threads[0]['name']}覆盖"
            f" {main_threads[0]['paper_count']} 篇，{main_threads[1]['name']}覆盖"
            f" {main_threads[1]['paper_count']} 篇。"
        )
    else:
        comparison = f"高分论文目前主要围绕{thread_text or '单一主线'}展开。"
    comparison += (
        f"跨论文重复出现的方法是{'、'.join(shared_methods)}。"
        if shared_methods
        else "尚未发现至少两篇高分论文共同采用的受控方法信号。"
    )
    deep_coverage = len(deep) / len(selected) if selected else 0.0
    if deep_coverage < 0.5:
        evidence_conclusion = (
            f"高分样本的全文深读覆盖率仅为 {deep_coverage * 100:.0f}%，"
            "以下判断以摘要和已有分析为主，应把它视为阅读导航而不是定论。"
        )
    elif shared_evidence:
        evidence_conclusion = (
            f"高分样本的全文深读覆盖率为 {deep_coverage * 100:.0f}%；"
            f"重复证据形态包括{'、'.join(shared_evidence)}，但仍需逐篇核对评测协议与失败案例。"
        )
    else:
        evidence_conclusion = (
            f"高分样本的全文深读覆盖率为 {deep_coverage * 100:.0f}%，"
            "但证据形态尚未形成跨论文重复，当前更适合比较思路而非下结论。"
        )
    return {
        "score_threshold": score_threshold,
        "qualified_paper_count": len(qualified),
        "included_paper_count": len(selected),
        "analyzed_paper_count": len(analyzed),
        "deep_analysis_count": len(deep),
        "lead": lead,
        "main_threads": main_threads,
        "paper_findings": [_paper_overview_item(record) for record in selected],
        "comparison": comparison,
        "evidence_conclusion": evidence_conclusion,
    }


def build_daily_research_report(
    session: Session,
    profile_id: int,
    *,
    report_date: date | datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    highlight_limit: int = 6,
    reading_queue_limit: int = 10,
    custom_method_terms: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a complete, evidence-aware daily report without remote calls."""

    timezone = _as_timezone(timezone_name)
    day = _target_day(report_date, timezone)
    day_start, day_end = _day_bounds(day, timezone)
    previous_start, _ = _day_bounds(day - timedelta(days=1), timezone)
    baseline_start, _ = _day_bounds(day - timedelta(days=7), timezone)
    all_records = _load_records(session, profile_id)
    candidate_records = _within(all_records, day_start, day_end)
    records = [record for record in candidate_records if record.labels]
    previous_candidates = _within(all_records, previous_start, day_start)
    previous = [record for record in previous_candidates if record.labels]
    recent_baseline = [
        record for record in _within(all_records, baseline_start, day_start) if record.labels
    ]
    daily_trend = _period_payload(
        candidate_records,
        previous_candidates,
        granularity="day",
        current_start=day,
        current_end=day,
        previous_start=day - timedelta(days=1),
        previous_end=day - timedelta(days=1),
        limit=DEFAULT_TREND_LIMIT,
    )
    ordered = _sort_records(records)

    def method_extractor(record: _PaperSignal) -> list[str]:
        return _method_labels(record, custom_method_terms)

    methods = [item for item in _aggregate_items(records, method_extractor) if item["shared"]][:12]
    evidence = _aggregate_items(records, _evidence_labels)[:12]
    analyzed_count = sum(record.analysis is not None for record in records)
    deep_count = sum(
        record.analysis is not None and record.analysis.kind == "deep" for record in records
    )
    topic_insights = _topic_insights(daily_trend["topics"])
    research_situation = _research_situation(
        day,
        records,
        previous,
        recent_baseline,
        daily_trend["topics"],
        methods,
        evidence,
    )
    research_situation["quality_overview"] = _quality_overview(records)
    return {
        "profile_id": profile_id,
        "report_date": day.isoformat(),
        "timezone": timezone_name,
        "title": f"{day.isoformat()} 当日研究总结",
        "date_basis": ARXIV_BATCH_DATE_BASIS,
        "coverage": {
            "paper_count": len(records),
            "candidate_paper_count": len(candidate_records),
            "direction_attributed_paper_count": len(records),
            "relevant_paper_count": len(records),
            "direction_attribution_coverage_percent": (
                round(len(records) / len(candidate_records) * 100.0, 1)
                if candidate_records
                else 0.0
            ),
            "analyzed_paper_count": analyzed_count,
            "deep_analysis_count": deep_count,
            "analysis_coverage_percent": (
                round(analyzed_count / len(records) * 100.0, 1) if records else 0.0
            ),
            "deep_analysis_coverage_percent": (
                round(deep_count / len(records) * 100.0, 1) if records else 0.0
            ),
            "high_score_paper_count": sum(_score(record.match) >= 80 for record in records),
        },
        "executive_summary": _executive_summary(
            day,
            records,
            daily_trend["topics"],
            candidate_count=len(candidate_records),
        ),
        "research_situation": research_situation,
        "topic_insights": topic_insights,
        "highlight_papers": [_highlight(record) for record in ordered[: max(0, highlight_limit)]],
        "common_methods": methods,
        "common_evidence": evidence,
        # Paper-specific weaknesses lose meaning when detached from their paper.
        # Keep them in the quality overview and paper detail instead.
        "limitations_and_research_gaps": [],
        "suggested_reading_queue": _reading_queue(ordered, reading_queue_limit),
        "daily_trend": daily_trend,
        "methodology_note": (
            "论文按 arXiv submittedDate 获取，并按设置时区归入报告日期。"
            "研究态势、趋势、共同方法与证据、代表论文及阅读队列仅使用能归入"
            "当前正式研究方向的论文；候选总量仅用于说明抓取覆盖。报告优先使用已保存的"
            "深度分析，缺失时仅依据题目、摘要、正式研究方向、归因关键词和受控术语生成"
            "可核查线索，不将摘要未提及的信息视为论文事实。"
        ),
    }


__all__ = [
    "DEFAULT_TIMEZONE",
    "build_daily_research_report",
    "build_research_roadmap",
    "build_topic_trends",
    "current_date_in_timezone",
]
