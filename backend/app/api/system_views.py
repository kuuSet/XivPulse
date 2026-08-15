"""Shared system, settings, and provider response views."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Integer, desc, func, select
from sqlalchemy.orm import Session

from ..config import get_config, provider_status
from ..env import xivpulse_env
from ..models import Analysis, DailyResearchReport, Paper, ReportSynthesisRun
from ..services.mailer import email_enabled
from ..services.prompt_catalog import resolve_prompt_settings
from ..services.semantic import RECALL_THRESHOLDS, embedding_status, recall_mode
from ..services.state import clean_string_list, setting_map
from .presenters import _as_utc


def _settings_wire(db: Session) -> dict[str, Any]:
    values = setting_map(db)
    config = get_config()
    saved_key = values.get("api_key")
    base_url = values.get("base_url") or config.base_url
    model = values.get("model") or config.model
    analysis_budget_mode = str(values.get("analysis_budget_mode") or "balanced")
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
    prompt_settings = resolve_prompt_settings(values)
    screen_batch_size = int(values.get("screen_batch_size", values.get("screen_top_n", 30)) or 0)
    smtp_password = str(xivpulse_env("SMTP_PASSWORD") or values.get("smtp_password") or "")
    return {
        "provider": values.get("provider_name") or config.provider_name,
        "provider_name": values.get("provider_name") or config.provider_name,
        "base_url": base_url,
        "model": model,
        "api_key_configured": bool(saved_key or config.api_key),
        "source": "database"
        if any(key in values for key in ("base_url", "model", "api_key"))
        else config.config_source,
        "config_source": config.config_source,
        "key_source": "database" if saved_key else config.key_source,
        "refresh_time": values.get("schedule_time", "13:30"),
        "schedule_time": values.get("schedule_time", "13:30"),
        "timezone": values.get("timezone", "Asia/Shanghai"),
        "max_candidates": values.get("max_results", 1000),
        "max_results": values.get("max_results", 1000),
        "screen_top_n": screen_batch_size,
        "screen_batch_size": screen_batch_size,
        "semantic_recall_mode": recall_mode(values.get("semantic_recall_mode")),
        "semantic_recall_presets": {
            mode: {"semantic_threshold": RECALL_THRESHOLDS[mode]}
            for mode in ("high", "balanced", "precise")
        },
        "embedding": {
            **embedding_status(),
            "backfill": values.get("embedding_backfill", {"status": "pending"}),
        },
        "deep_analysis_limit": values.get("deep_analysis_top_n", 5),
        "deep_analysis_top_n": values.get("deep_analysis_top_n", 5),
        "deep_relevance_threshold": values.get("deep_relevance_threshold", 90),
        "retention_days": values.get("retention_days", 365),
        "analysis_budget_mode": analysis_budget_mode,
        "daily_ai_synthesis_enabled": bool(values.get("daily_ai_synthesis_enabled", True)),
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
        "mock_mode": not bool(saved_key or config.api_key),
        "email_enabled": email_enabled(values),
        "smtp_host": str(xivpulse_env("SMTP_HOST") or values.get("smtp_host") or ""),
        "smtp_port": int(xivpulse_env("SMTP_PORT") or values.get("smtp_port") or 587),
        "smtp_security": str(
            xivpulse_env("SMTP_SECURITY") or values.get("smtp_security") or "starttls"
        ),
        "smtp_username": str(xivpulse_env("SMTP_USERNAME") or values.get("smtp_username") or ""),
        "smtp_password_configured": bool(smtp_password),
        "smtp_from": str(xivpulse_env("SMTP_FROM") or values.get("smtp_from") or ""),
        "email_recipients": clean_string_list(
            [
                item.strip()
                for item in str(xivpulse_env("EMAIL_RECIPIENTS", "")).split(",")
                if item.strip()
            ]
            or values.get("email_recipients")
        ),
        "public_base_url": str(
            xivpulse_env("PUBLIC_BASE_URL") or values.get("public_base_url") or ""
        ),
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
    settings = setting_map(db)
    public = provider_status()
    if settings.get("provider_name"):
        public["provider_name"] = settings["provider_name"]
    if settings.get("base_url"):
        public["base_url"] = settings["base_url"]
    if settings.get("model"):
        public["model"] = settings["model"]
    if settings.get("api_key"):
        public.update(
            api_key_configured=True,
            mock_mode=False,
            key_source="database",
            config_source="database",
        )
    return public
