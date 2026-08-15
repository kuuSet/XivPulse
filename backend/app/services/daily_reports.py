"""Persist, synthesize, and deliver the daily research report."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal, session_scope
from ..models import (
    DailyResearchReport,
    Digest,
    EmailDelivery,
    PaperMatch,
    ReportSynthesisRun,
    ScreeningQueueItem,
    utcnow,
)
from .analytics import build_daily_research_report, current_date_in_timezone
from .jobs import update_job_progress
from .mailer import build_daily_message, deliver_message, email_enabled, smtp_config
from .method_catalog import clean_custom_methods
from .provider import OpenAICompatibleAdapter, ProviderError
from .report_synthesis import (
    generate_daily_report_synthesis,
    report_source_hash,
    synthesis_has_enough_data,
)
from .state import active_profile, configured_provider_config, setting_map

LOG = logging.getLogger("xivpulse")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def create_digest(digest_date: date | None = None) -> dict[str, Any]:
    with session_scope() as db:
        profile = active_profile(db)
        matches = db.scalars(
            select(PaperMatch)
            .where(PaperMatch.profile_id == profile.id)
            .order_by(desc(PaperMatch.relevance_score))
            .limit(20)
        ).all()
        timezone_name = str(setting_map(db).get("timezone", "Asia/Shanghai"))
        today = digest_date or current_date_in_timezone(timezone_name)
        digest = db.scalar(
            select(Digest).where(Digest.profile_id == profile.id, Digest.digest_date == today)
        )
        if digest is None:
            digest = Digest(
                profile_id=profile.id,
                digest_date=today,
                title=f"{today.isoformat()} arXiv 每日精选",
            )
            db.add(digest)
        digest.paper_ids_json = [item.paper_id for item in matches]
        digest.summary = f"今日筛选出 {len(matches)} 篇与你的研究画像相关的论文。"
        digest.stats_json = {"recommended": len(matches)}
        digest.status = "ready"
        db.flush()
        return {"digest_id": digest.id, "recommended": len(matches)}


def report_snapshot(snapshot: DailyResearchReport) -> dict[str, Any]:
    return {
        **snapshot.payload,
        "persisted": True,
        "snapshot_id": snapshot.id,
        "generated_at": _as_utc(snapshot.created_at),
        "updated_at": _as_utc(snapshot.updated_at),
    }


def persist_daily_report(
    db: Session,
    profile_id: int,
    *,
    report_date: date | None = None,
    timezone_name: str,
    max_papers: int = 10,
    refresh: bool = False,
) -> DailyResearchReport:
    """Return the saved report for a day, creating or explicitly refreshing it."""

    day = report_date or current_date_in_timezone(timezone_name)
    snapshot = db.scalar(
        select(DailyResearchReport).where(
            DailyResearchReport.profile_id == profile_id,
            DailyResearchReport.report_date == day,
        )
    )
    if snapshot is not None and not refresh:
        return snapshot

    report = build_daily_research_report(
        db,
        profile_id,
        report_date=day,
        timezone_name=timezone_name,
        highlight_limit=min(max_papers, 10),
        reading_queue_limit=max_papers,
        custom_method_terms=clean_custom_methods(setting_map(db).get("custom_method_terms", [])),
    )
    encoded = jsonable_encoder(report)
    if not isinstance(encoded, dict):
        raise TypeError("Daily research report must serialize to a JSON object")
    previous_synthesis = snapshot.payload.get("ai_synthesis") if snapshot is not None else None
    if isinstance(previous_synthesis, dict):
        preserved_synthesis = dict(previous_synthesis)
        preserved_synthesis["stale"] = str(
            preserved_synthesis.get("source_hash") or ""
        ) != report_source_hash(encoded)
        encoded["ai_synthesis"] = preserved_synthesis
    if snapshot is None:
        snapshot = DailyResearchReport(
            profile_id=profile_id,
            report_date=day,
            payload_json=encoded,
        )
        db.add(snapshot)
    else:
        snapshot.payload_json = encoded
        snapshot.updated_at = utcnow()
    db.flush()
    return snapshot


def create_daily_report(
    *,
    report_date: date | None = None,
    screening_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh one deterministic report after the daily analysis pipeline."""

    with session_scope() as db:
        profile = active_profile(db)
        timezone_name = str(setting_map(db).get("timezone", "Asia/Shanghai"))
        selected_date = report_date or current_date_in_timezone(timezone_name)
        snapshot = persist_daily_report(
            db,
            profile.id,
            report_date=selected_date,
            timezone_name=timezone_name,
            refresh=True,
        )
        if screening_status:
            payload = snapshot.payload
            coverage = dict(payload.get("coverage") or {})
            coverage.update(
                {
                    "screening_queue_total": int(
                        screening_status.get("screening_queue_total", 0) or 0
                    ),
                    "screening_completed": int(screening_status.get("screening_completed", 0) or 0),
                    "screening_failed": int(screening_status.get("screening_failed", 0) or 0),
                }
            )
            payload["coverage"] = coverage
            payload["incomplete"] = bool(screening_status.get("report_incomplete", False))
            payload["screening_status"] = {
                "total": coverage["screening_queue_total"],
                "completed": coverage["screening_completed"],
                "failed": coverage["screening_failed"],
                "batches": int(screening_status.get("screening_batches", 0) or 0),
            }
            snapshot.payload_json = payload
            snapshot.updated_at = utcnow()
        return {
            "report_snapshot_id": snapshot.id,
            "report_date": snapshot.report_date.isoformat(),
        }


def _synthesis_state(
    previous: dict[str, Any] | None,
    *,
    status_name: str,
    source_hash: str,
    error: str | None = None,
) -> dict[str, Any]:
    state = dict(previous or {})
    previous_hash = str(state.get("source_hash") or "")
    state.update(
        {
            "status": status_name,
            "source_hash": source_hash,
            "stale": bool(previous_hash and previous_hash != source_hash),
        }
    )
    if error:
        state["last_error"] = error
    else:
        state.pop("last_error", None)
    return state


async def synthesize_daily_report(
    report_date: date,
    *,
    force: bool = False,
    respect_enabled: bool = True,
) -> dict[str, Any]:
    """Generate at most one saved synthesis for the current deterministic inputs."""

    with session_scope() as db:
        profile = active_profile(db)
        settings = setting_map(db)
        snapshot = db.scalar(
            select(DailyResearchReport).where(
                DailyResearchReport.profile_id == profile.id,
                DailyResearchReport.report_date == report_date,
            )
        )
        if snapshot is None:
            raise ValueError(f"没有找到 {report_date.isoformat()} 的研究日报")
        report = snapshot.payload
        previous_value = report.get("ai_synthesis")
        previous = dict(previous_value) if isinstance(previous_value, dict) else None
        source_hash = report_source_hash(report)
        enabled = bool(settings.get("daily_ai_synthesis_enabled", True))
        if respect_enabled and not enabled:
            if previous is None:
                report["ai_synthesis"] = _synthesis_state(
                    None, status_name="disabled", source_hash=source_hash
                )
                snapshot.payload_json = report
            return {"report_synthesis_status": "disabled", "report_date": report_date.isoformat()}
        if not synthesis_has_enough_data(report):
            report["ai_synthesis"] = _synthesis_state(
                previous, status_name="insufficient_data", source_hash=source_hash
            )
            snapshot.payload_json = report
            snapshot.updated_at = utcnow()
            return {
                "report_synthesis_status": "insufficient_data",
                "report_date": report_date.isoformat(),
            }
        if (
            not force
            and previous is not None
            and previous.get("status") == "ready"
            and previous.get("source_hash") == source_hash
        ):
            return {"report_synthesis_status": "ready", "report_date": report_date.isoformat()}
        config = configured_provider_config(settings)
        if config.mock_mode:
            report["ai_synthesis"] = _synthesis_state(
                previous, status_name="not_configured", source_hash=source_hash
            )
            snapshot.payload_json = report
            snapshot.updated_at = utcnow()
            return {
                "report_synthesis_status": "not_configured",
                "report_date": report_date.isoformat(),
            }
        budget_mode = str(settings.get("analysis_budget_mode") or "balanced")
        run = ReportSynthesisRun(
            profile_id=profile.id,
            report_id=snapshot.id,
            report_date=report_date,
            status="running",
            source_hash=source_hash,
            provider=config.provider_name,
            model=config.model,
            transport=config.api_mode,
            budget_mode=budget_mode,
        )
        db.add(run)
        db.flush()
        run_id = run.id

    try:
        generated = await generate_daily_report_synthesis(
            OpenAICompatibleAdapter(config),
            report,
            budget_mode=budget_mode,
        )
        usage = generated.usage
        generated_at = utcnow()
        synthesis = {
            "status": "ready",
            **generated.data.model_dump(),
            "generated_at": generated_at.isoformat(),
            "provider": generated.provider,
            "model": generated.model,
            "transport": generated.transport,
            "budget_mode": budget_mode,
            "source_hash": source_hash,
            "stale": False,
            "usage": {
                "available": usage.available,
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
            },
        }
        with session_scope() as db:
            run = db.get(ReportSynthesisRun, run_id)
            snapshot = db.get(DailyResearchReport, run.report_id) if run is not None else None
            if run is None or snapshot is None:
                raise RuntimeError("日报或研判记录在生成过程中被删除")
            run.status = "ready"
            run.transport = generated.transport
            run.provider = generated.provider
            run.model = generated.model
            run.payload_json = synthesis
            for key, value in usage.analysis_fields(budget_mode).items():
                setattr(run, key, value)
            payload = snapshot.payload
            payload["ai_synthesis"] = synthesis
            snapshot.payload_json = payload
            snapshot.updated_at = generated_at
        return {"report_synthesis_status": "ready", "report_date": report_date.isoformat()}
    except Exception as exc:
        LOG.exception("Daily report AI synthesis failed for %s", report_date)
        safe_error = (
            str(exc)[:500]
            if isinstance(exc, ProviderError)
            else "AI 综合研判生成失败，请检查模型配置或稍后重试"
        )
        with session_scope() as db:
            run = db.get(ReportSynthesisRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = safe_error
                snapshot = db.get(DailyResearchReport, run.report_id)
                if snapshot is not None:
                    payload = snapshot.payload
                    payload["ai_synthesis"] = _synthesis_state(
                        previous,
                        status_name="error",
                        source_hash=source_hash,
                        error=safe_error,
                    )
                    snapshot.payload_json = payload
                    snapshot.updated_at = utcnow()
        return {
            "report_synthesis_status": "error",
            "report_synthesis_error": safe_error,
            "report_date": report_date.isoformat(),
        }


async def send_daily_report_email(
    report_date: date,
    *,
    delivery_kind: str = "automatic",
    require_enabled: bool = True,
) -> dict[str, Any]:
    with SessionLocal() as db:
        profile = active_profile(db)
        settings = setting_map(db)
        if require_enabled and not email_enabled(settings):
            return {"email_status": "disabled", "email_sent": 0, "email_failed": 0}
        config = smtp_config(settings)
        if not config.ready:
            return {
                "email_status": "not_configured",
                "email_sent": 0,
                "email_failed": 0,
            }
        snapshot = db.scalar(
            select(DailyResearchReport).where(
                DailyResearchReport.profile_id == profile.id,
                DailyResearchReport.report_date == report_date,
            )
        )
        if snapshot is None:
            raise ValueError(f"没有找到 {report_date.isoformat()} 的研究日报")
        report = snapshot.payload
        profile_id = profile.id
        recipients = config.recipients

    sent = 0
    failed = 0
    errors: list[str] = []
    for recipient in recipients:
        with session_scope() as db:
            delivery = db.scalar(
                select(EmailDelivery).where(
                    EmailDelivery.profile_id == profile_id,
                    EmailDelivery.report_date == report_date,
                    EmailDelivery.recipient == recipient,
                    EmailDelivery.delivery_kind == delivery_kind,
                )
            )
            if delivery is not None and delivery.status == "sent":
                continue
            if delivery is None:
                delivery = EmailDelivery(
                    profile_id=profile_id,
                    report_date=report_date,
                    recipient=recipient,
                    delivery_kind=delivery_kind,
                )
                db.add(delivery)
                db.flush()
            delivery.status = "sending"
            delivery.attempts += 1
            delivery_id = delivery.id
        try:
            failure_count = int(dict(report.get("coverage") or {}).get("screening_failed", 0) or 0)
            message = build_daily_message(
                config,
                report,
                recipient,
                incomplete=bool(report.get("incomplete", False)),
                failure_count=failure_count,
            )
            await asyncio.to_thread(deliver_message, config, message)
            with session_scope() as db:
                delivery = db.get(EmailDelivery, delivery_id)
                if delivery is not None:
                    delivery.status = "sent"
                    delivery.subject = str(message["Subject"] or "")
                    delivery.last_error = None
                    delivery.sent_at = utcnow()
            sent += 1
        except Exception as exc:
            LOG.exception("Daily report email failed for %s", recipient)
            with session_scope() as db:
                delivery = db.get(EmailDelivery, delivery_id)
                if delivery is not None:
                    delivery.status = "failed"
                    delivery.last_error = str(exc)
            failed += 1
            errors.append(f"{recipient}: {exc}")
    status_name = "sent" if sent and not failed else "failed" if failed else "duplicate"
    return {
        "email_status": status_name,
        "email_sent": sent,
        "email_failed": failed,
        "email_errors": errors[:10],
    }


async def finalize_daily_stage(
    pipeline_date: date,
    result: dict[str, Any],
    job_id: str | None,
) -> dict[str, Any]:
    update_job_progress(job_id, progress=88, message="正在重排推荐并保存当日研究日报")
    result.update(create_digest(pipeline_date))
    result.update(create_daily_report(report_date=pipeline_date, screening_status=result))
    current_report_date = date.fromisoformat(str(result["report_date"]))
    refreshed_report_dates: list[str] = []
    for value in result.get("screening_report_dates", []):
        selected_date = date.fromisoformat(str(value))
        if selected_date == current_report_date:
            continue
        with SessionLocal() as db:
            profile = active_profile(db)
            status_rows = dict(
                db.execute(
                    select(ScreeningQueueItem.status, func.count(ScreeningQueueItem.id))
                    .where(
                        ScreeningQueueItem.profile_id == profile.id,
                        ScreeningQueueItem.report_date == selected_date,
                    )
                    .group_by(ScreeningQueueItem.status)
                ).all()
            )
        total = sum(int(count) for count in status_rows.values())
        failed = int(status_rows.get("failed", 0))
        create_daily_report(
            report_date=selected_date,
            screening_status={
                "screening_queue_total": total,
                "screening_completed": int(status_rows.get("completed", 0)),
                "screening_failed": failed,
                "screening_batches": 0,
                "report_incomplete": failed > 0,
            },
        )
        refreshed_report_dates.append(selected_date.isoformat())
    result["refreshed_report_dates"] = refreshed_report_dates
    update_job_progress(job_id, progress=94, message="正在生成并保存 AI 综合研判")
    result.update(
        await synthesize_daily_report(
            current_report_date,
            force=False,
            respect_enabled=True,
        )
    )
    result.update(
        await send_daily_report_email(
            current_report_date,
            delivery_kind="automatic",
        )
    )
    return result
