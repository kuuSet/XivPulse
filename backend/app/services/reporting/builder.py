"""Score- and evidence-based daily research report construction."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.orm import Session

from ...modules.papers.organizations import paper_organizations
from .records import (
    ARXIV_BATCH_DATE_BASIS,
    DAILY_QUALITY_OVERVIEW_LIMIT,
    DAILY_QUALITY_SCORE_THRESHOLD,
    DEFAULT_TIMEZONE,
    _as_timezone,
    _clean_phrase,
    _day_bounds,
    _key,
    _load_records,
    _mean,
    _PaperSignal,
    _payload,
    _payload_list,
    _quality,
    _score,
    _sort_records,
    _target_day,
    _within,
)
from .signals import (
    _evidence_items,
    _evidence_labels,
    _method_items,
    _unique_phrases,
)


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
    return _clean_phrase(record.paper.abstract, max_length=360) or "当前记录暂无摘要或分析。"


def _highlight(record: _PaperSignal) -> dict[str, Any]:
    payload = _payload(record.analysis)
    return {
        "paper_id": record.paper.id,
        "arxiv_id": record.paper.arxiv_id,
        "title": record.paper.title,
        "authors": record.paper.authors,
        "notable_organizations": paper_organizations(record.paper),
        "published_at": record.timestamp.isoformat(),
        "score": _score(record.match),
        "quality_score": _quality(record.analysis),
        "summary": _summary(record),
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
            next_action = "仅保留记录，暂不投入深读时间"
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
            }
        )
    return result


def _executive_summary(day: date, records: Sequence[_PaperSignal]) -> str:
    if not records:
        return f"{day.isoformat()} 没有已完成摘要粗排的论文。建议确认当日抓取和粗排任务状态。"
    analyzed = sum(record.analysis is not None for record in records)
    high_score = sum(_score(record.match) >= DAILY_QUALITY_SCORE_THRESHOLD for record in records)
    average_score = _mean(_score(record.match) for record in records) or 0.0
    return (
        f"{day.isoformat()} 共纳入 {len(records)} 篇已粗排论文，平均推荐分 {average_score:.1f}；"
        f"其中 {high_score} 篇不低于 {DAILY_QUALITY_SCORE_THRESHOLD:.0f} 分，"
        f"{analyzed} 篇已有可用分析。"
    )


def _research_situation(
    day: date,
    records: Sequence[_PaperSignal],
    previous: Sequence[_PaperSignal],
    recent_baseline: Sequence[_PaperSignal],
    methods: Sequence[dict[str, Any]],
) -> dict[str, Any]:
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

    deep_records = [
        record
        for record in records
        if record.analysis is not None and record.analysis.kind == "deep"
    ]
    strong_evidence = {"真实机器人实验", "消融实验", "跨环境泛化", "理论分析", "用户研究"}
    deep_evidence = _aggregate_items(deep_records, _evidence_labels)
    shared_strong_evidence = [
        str(item["name"])
        for item in deep_evidence
        if str(item["name"]) in strong_evidence and int(item["count"]) >= 2
    ]
    deep_coverage = len(deep_records) / len(records) if records else 0.0
    if not records:
        evidence_status = "no_signal"
        evidence_text = "当日没有已粗排论文，无法形成新的证据判断"
    elif deep_coverage < 0.5:
        evidence_status = "insufficient_analysis"
        evidence_text = "多数论文尚未完成深度分析，方法与证据判断仍不充分"
    elif deep_coverage >= 0.75 and len(shared_strong_evidence) >= 2:
        evidence_status = "strong"
        evidence_text = "多类较强证据均得到至少两篇论文支持，且深度分析覆盖充分"
    elif shared_strong_evidence:
        evidence_status = "mixed"
        evidence_text = "已经出现跨论文的较强证据，但证据类型或覆盖范围仍有限"
    else:
        evidence_status = "preliminary"
        evidence_text = "证据尚未形成跨论文重复，结论仍偏初步"

    current_average = _mean(_score(record.match) for record in records)
    previous_average = _mean(_score(record.match) for record in previous)
    highest_score = max((_score(record.match) for record in records), default=None)
    score_change = (
        round(current_average - previous_average, 1)
        if current_average is not None and previous_average is not None
        else None
    )
    high_score_count = sum(
        _score(record.match) >= DAILY_QUALITY_SCORE_THRESHOLD for record in records
    )
    method_names = [str(item["name"]) for item in methods[:3]]
    if not records:
        synthesis = (
            f"{day.isoformat()} 没有已完成摘要粗排的论文；建议把它视为无数据日，"
            "先确认流水线状态，再判断研究活动变化。"
        )
    else:
        volume_direction = (
            "增加"
            if current_count > previous_count
            else "减少"
            if current_count < previous_count
            else "持平"
        )
        synthesis = (
            f"当日共纳入 {current_count} 篇，较昨日{volume_direction} "
            f"{abs(current_count - previous_count)} 篇，{volume_text}。"
            f"平均推荐分 {current_average or 0.0:.1f}，其中 {high_score_count} 篇不低于 "
            f"{DAILY_QUALITY_SCORE_THRESHOLD:.0f} 分。"
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
        "score_summary": {
            "average": current_average,
            "previous_average": previous_average,
            "change": score_change,
            "highest": highest_score,
            "high_score_count": high_score_count,
            "high_score_threshold": DAILY_QUALITY_SCORE_THRESHOLD,
        },
        "method_signals": method_names,
        "evidence_maturity": {
            "status": evidence_status,
            "assessment": evidence_text,
            "strong_signal_count": len(shared_strong_evidence),
            "shared_strong_signals": shared_strong_evidence,
            "deep_analysis_coverage_percent": round(deep_coverage * 100.0, 1),
        },
    }


def _first_sentence(value: str, *, max_length: int = 220) -> str:
    text = _clean_phrase(value, max_length=max_length)
    if not text:
        return ""
    return re.split(r"(?<=[。！？.!?])\s+", text, maxsplit=1)[0].strip()


def _paper_overview_item(record: _PaperSignal) -> dict[str, Any]:
    payload = _payload(record.analysis)
    contributions = _payload_list(payload, "key_contributions")
    contribution = _first_sentence(contributions[0]) if contributions else ""
    contribution = contribution or _first_sentence(_summary(record))
    methods = _method_items(record)
    method = "、".join(_first_sentence(value, max_length=140) for value in methods[:3])
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
            "score_range": None,
            "lead": f"当日没有推荐分达到 {score_threshold:.0f} 的论文。",
            "paper_findings": [],
            "comparison": "没有足够的高分样本进行横向比较。",
            "evidence_conclusion": "没有足够的高分样本支持当日优质论文综述。",
        }

    scores = [_score(record.match) for record in selected]
    shared_methods = [
        str(item["name"])
        for item in _aggregate_items(selected, _method_items)
        if bool(item["shared"])
    ][:4]
    shared_evidence = [
        str(item["name"])
        for item in _aggregate_items(selected, _evidence_labels)
        if bool(item["shared"])
    ][:4]
    lead = (
        f"当日 {len(records)} 篇已粗排论文中有 {len(qualified)} 篇达到推荐分 "
        f"{score_threshold:.0f}；本总览比较排名前 {len(selected)} 篇。"
        f"其中 {len(analyzed)} 篇已有 AI 分析、{len(deep)} 篇完成全文深读。"
    )
    comparison = (
        f"高分论文平均推荐分为 {_mean(scores) or 0.0:.1f}，"
        f"分布在 {min(scores):.1f}–{max(scores):.1f} 分。"
    )
    comparison += (
        f"跨论文重复出现的方法是{'、'.join(shared_methods)}。"
        if shared_methods
        else "尚未发现至少两篇高分论文共同采用的受控方法信号。"
    )
    deep_coverage = len(deep) / len(selected)
    if deep_coverage < 0.5:
        evidence_conclusion = (
            f"高分样本的全文深读覆盖率仅为 {deep_coverage * 100:.0f}%，"
            "以下判断以摘要和已有分析为主，应把它视为阅读导航而不是定论。"
        )
    elif shared_evidence:
        evidence_conclusion = (
            f"高分样本的全文深读覆盖率为 {deep_coverage * 100:.0f}%；"
            f"重复证据形态包括{'、'.join(shared_evidence)}，但仍需核对评测协议与失败案例。"
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
        "score_range": {
            "lowest": min(scores),
            "highest": max(scores),
            "average": _mean(scores),
        },
        "lead": lead,
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
) -> dict[str, Any]:
    """Generate a deterministic daily report from screening scores and saved analyses."""

    timezone = _as_timezone(timezone_name)
    day = _target_day(report_date, timezone)
    day_start, day_end = _day_bounds(day, timezone)
    previous_start, _ = _day_bounds(day - timedelta(days=1), timezone)
    baseline_start, _ = _day_bounds(day - timedelta(days=7), timezone)
    all_records = _load_records(session, profile_id)
    records = _within(all_records, day_start, day_end)
    previous = _within(all_records, previous_start, day_start)
    recent_baseline = _within(all_records, baseline_start, day_start)
    ordered = _sort_records(records)

    methods = [item for item in _aggregate_items(records, _method_items) if item["shared"]][:12]
    evidence = _aggregate_items(records, _evidence_labels)[:12]
    analyzed_count = sum(record.analysis is not None for record in records)
    deep_count = sum(
        record.analysis is not None and record.analysis.kind == "deep" for record in records
    )
    research_situation = _research_situation(day, records, previous, recent_baseline, methods)
    research_situation["quality_overview"] = _quality_overview(records)
    return {
        "report_date": day.isoformat(),
        "timezone": timezone_name,
        "title": f"{day.isoformat()} 当日研究总结",
        "date_basis": ARXIV_BATCH_DATE_BASIS,
        "coverage": {
            "paper_count": len(records),
            "analyzed_paper_count": analyzed_count,
            "deep_analysis_count": deep_count,
            "analysis_coverage_percent": (
                round(analyzed_count / len(records) * 100.0, 1) if records else 0.0
            ),
            "deep_analysis_coverage_percent": (
                round(deep_count / len(records) * 100.0, 1) if records else 0.0
            ),
            "high_score_paper_count": sum(
                _score(record.match) >= DAILY_QUALITY_SCORE_THRESHOLD for record in records
            ),
            "average_score": _mean(_score(record.match) for record in records),
        },
        "executive_summary": _executive_summary(day, records),
        "research_situation": research_situation,
        "highlight_papers": [_highlight(record) for record in ordered[: max(0, highlight_limit)]],
        "common_methods": methods,
        "common_evidence": evidence,
        "suggested_reading_queue": _reading_queue(ordered, reading_queue_limit),
        "methodology_note": (
            "论文按 arXiv submittedDate 获取，并按设置时区归入报告日期。"
            "报告纳入当日已完成摘要粗排的论文，推荐顺序只使用粗排分数；"
            "方法只使用已保存的深度分析结果；证据优先使用深度分析，缺失时仅从题目"
            "和摘要提取可核查线索，不根据关键词或 arXiv 分类伪造方法或关注标签归因。"
        ),
    }
