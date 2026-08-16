"""Shared system, settings, and provider response views."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Integer, desc, func, select
from sqlalchemy.orm import Session

from ..config import load_runtime_settings, setting_value
from ..models import Analysis, DailyResearchReport, Paper, ReportSynthesisRun
from ..services.prompt_catalog import resolve_prompt_settings
from ..services.semantic import RECALL_THRESHOLDS, embedding_status, recall_mode
from .presenters import _as_utc


def _settings_wire(db: Session) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    config = settings.provider
    bind = db.get_bind()
    database_path: str | None = None
    database_size_bytes: int | None = None
    if bind.dialect.name == "sqlite" and bind.url.database not in (None, ":memory:"):
        try:
            path = Path(str(bind.url.database)).resolve()
            database_path = str(path)
            database_size_bytes = path.stat().st_size if path.exists() else 0
        except (OSError, ValueError):
            pass
    prompt_settings = resolve_prompt_settings(settings.prompt_values())
    return {
        "provider_name": config.provider_name,
        "base_url": config.base_url,
        "model": config.model,
        "api_key_configured": not config.mock_mode,
        "schedule_time": settings.schedule_time,
        "timezone": settings.timezone,
        "max_results": settings.max_results,
        "screen_batch_size": settings.screen_batch_size,
        "semantic_recall_mode": recall_mode(settings.semantic_recall_mode),
        "semantic_recall_presets": {
            mode: {"semantic_threshold": RECALL_THRESHOLDS[mode]}
            for mode in ("high", "balanced", "precise")
        },
        "embedding": {
            **embedding_status(),
            "backfill": setting_value(db, "embedding_backfill", {"status": "pending"}),
        },
        "deep_analysis_top_n": settings.deep_analysis_top_n,
        "deep_relevance_threshold": settings.deep_relevance_threshold,
        "retention_days": settings.retention_days,
        "analysis_budget_mode": settings.analysis_budget_mode,
        "daily_ai_synthesis_enabled": settings.daily_ai_synthesis_enabled,
        **prompt_settings,
        "automatic_cleanup_enabled": False,
        "database_path": database_path,
        "database_size_bytes": database_size_bytes,
        "stored_paper_count": db.scalar(select(func.count()).select_from(Paper)) or 0,
        "stored_analysis_count": db.scalar(select(func.count()).select_from(Analysis)) or 0,
        "stored_report_count": (
            db.scalar(select(func.count()).select_from(DailyResearchReport)) or 0
        ),
        "token_usage": _token_usage_summary(db),
        "api_mode": config.api_mode,
        "mock_mode": config.mock_mode,
        "email_enabled": settings.email_enabled,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_security": settings.smtp_security,
        "smtp_username": settings.smtp_username,
        "smtp_password_configured": bool(settings.smtp_password),
        "smtp_from": settings.smtp_from,
        "email_recipients": list(settings.email_recipients),
        "public_base_url": settings.public_base_url,
    }


def _token_usage_summary(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(
            Analysis.kind,
            func.count(Analysis.id),
            func.sum(Analysis.input_tokens),
            func.sum(Analysis.cached_input_tokens),
            func.sum(Analysis.output_tokens),
            func.sum(Analysis.reasoning_tokens),
            func.sum(Analysis.total_tokens),
            func.sum(func.cast(Analysis.usage_available, Integer)),
        ).group_by(Analysis.kind)
    ).all()
    by_kind: dict[str, dict[str, Any]] = {}
    total_records = 0
    available_records = 0
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    for row in rows:
        kind = str(row[0])
        record_count = int(row[1] or 0)
        usage_count = int(row[7] or 0)
        item = {
            "record_count": record_count,
            "usage_available_count": usage_count,
            "usage_missing_count": record_count - usage_count,
            "input_tokens": int(row[2] or 0),
            "cached_input_tokens": int(row[3] or 0),
            "output_tokens": int(row[4] or 0),
            "reasoning_tokens": int(row[5] or 0),
            "total_tokens": int(row[6] or 0),
        }
        by_kind[kind] = item
        total_records += record_count
        available_records += usage_count
        for key in totals:
            totals[key] += item[key]
    report_row = db.execute(
        select(
            func.count(ReportSynthesisRun.id),
            func.sum(ReportSynthesisRun.input_tokens),
            func.sum(ReportSynthesisRun.cached_input_tokens),
            func.sum(ReportSynthesisRun.output_tokens),
            func.sum(ReportSynthesisRun.reasoning_tokens),
            func.sum(ReportSynthesisRun.total_tokens),
            func.sum(func.cast(ReportSynthesisRun.usage_available, Integer)),
        )
    ).one()
    report_record_count = int(report_row[0] or 0)
    if report_record_count:
        report_usage_count = int(report_row[6] or 0)
        report_item = {
            "record_count": report_record_count,
            "usage_available_count": report_usage_count,
            "usage_missing_count": report_record_count - report_usage_count,
            "input_tokens": int(report_row[1] or 0),
            "cached_input_tokens": int(report_row[2] or 0),
            "output_tokens": int(report_row[3] or 0),
            "reasoning_tokens": int(report_row[4] or 0),
            "total_tokens": int(report_row[5] or 0),
        }
        by_kind["report_synthesis"] = report_item
        total_records += report_record_count
        available_records += report_usage_count
        for key in totals:
            totals[key] += report_item[key]

    latest_analysis = db.scalar(
        select(Analysis)
        .where(Analysis.usage_available.is_(True))
        .order_by(desc(Analysis.created_at), desc(Analysis.id))
    )
    latest_report = db.scalar(
        select(ReportSynthesisRun)
        .where(ReportSynthesisRun.usage_available.is_(True))
        .order_by(desc(ReportSynthesisRun.created_at), desc(ReportSynthesisRun.id))
    )
    latest: Analysis | ReportSynthesisRun | None = latest_analysis
    if latest_report is not None and (
        latest is None or _as_utc(latest_report.created_at) >= _as_utc(latest.created_at)
    ):
        latest = latest_report
    latest_kind = (
        "report_synthesis"
        if isinstance(latest, ReportSynthesisRun)
        else latest.kind
        if latest is not None
        else None
    )
    return {
        **totals,
        "record_count": total_records,
        "usage_available_count": available_records,
        "usage_missing_count": total_records - available_records,
        "by_kind": by_kind,
        "latest": (
            {
                "analysis_id": latest.id,
                "record_id": latest.id,
                "kind": latest_kind,
                "budget_mode": latest.budget_mode,
                "input_tokens": latest.input_tokens,
                "cached_input_tokens": latest.cached_input_tokens,
                "output_tokens": latest.output_tokens,
                "reasoning_tokens": latest.reasoning_tokens,
                "total_tokens": latest.total_tokens,
                "created_at": latest.created_at,
            }
            if latest is not None
            else None
        ),
    }


def _provider_public_status(db: Session) -> dict[str, Any]:
    return load_runtime_settings(db).provider.public_dict()
