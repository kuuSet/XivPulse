"""Research-direction roadmap construction."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Any

from sqlalchemy.orm import Session

from .records import (
    ARXIV_BATCH_DATE_BASIS,
    DEFAULT_TIMEZONE,
    _Label,
    _PaperSignal,
    _as_timezone,
    _day_bounds,
    _load_records,
    _mean,
    _quality,
    _representative_papers,
    _score,
    _target_day,
    _topic_id,
    _within,
)
from .signals import _has_term, _method_labels


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
