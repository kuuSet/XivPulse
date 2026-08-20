"""Persist, synthesize, and deliver the daily research report."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ...database import SessionLocal, session_scope
from ...models import (
    DailyResearchReport,
    Digest,
    EmailDelivery,
    Paper,
    PaperMatch,
    ReportSynthesisRun,
    utcnow,
)
from ...settings import load_runtime_settings
from ..jobs import update_job_progress
from ..mailer import build_daily_message, deliver_message, email_enabled, smtp_settings
from ..methods.catalog import clean_custom_methods
from ..provider import create_model_adapter
from ..state import active_profile
from .builder import build_daily_research_report
from .records import current_date_in_timezone
from .synthesis import (
    generate_daily_report_synthesis,
    report_source_hash,
    synthesis_has_enough_data,
)

LOG = logging.getLogger("xivpulse")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def create_digest(digest_date: date | None = None) -> dict[str, Any]:
    with session_scope() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        today = digest_date or current_date_in_timezone(settings.schedule_timezone)
        start = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
        end = datetime.combine(today, datetime.max.time(), tzinfo=UTC)
        matches = db.scalars(
            select(PaperMatch)
            .where(
                PaperMatch.profile_id == profile.id,
                PaperMatch.screened_at.is_not(None),
                PaperMatch.paper.has((Paper.published_at >= start) & (Paper.published_at <= end)),
            )
            .order_by(desc(PaperMatch.effective_score_expression()))
            .limit(20)
        ).all()
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
        digest.summary = f"今日筛选出 {len(matches)} 篇符合关注标签的论文。"
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
        custom_method_terms=clean_custom_methods(list(load_runtime_settings(db).custom_methods)),
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
    pipeline_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh one deterministic report after the daily analysis pipeline."""

    with session_scope() as db:
        profile = active_profile(db)
        timezone_name = load_runtime_settings(db).schedule_timezone
        selected_date = report_date or current_date_in_timezone(timezone_name)
        snapshot = persist_daily_report(
            db,
            profile.id,
            report_date=selected_date,
            timezone_name=timezone_name,
            refresh=True,
        )
        if pipeline_status:
            payload = snapshot.payload
            coverage = dict(payload.get("coverage") or {})
            coverage.update(
                {
                    "fetched": int(pipeline_status.get("fetched", 0) or 0),
                    "prefiltered": int(pipeline_status.get("prefiltered", 0) or 0),
                    "prefilter_kept": int(pipeline_status.get("prefilter_kept", 0) or 0),
                    "screened": int(pipeline_status.get("screened", 0) or 0),
                    "deep_analyzed": int(pipeline_status.get("analyzed", 0) or 0),
                }
            )
            payload["coverage"] = coverage
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
        settings = load_runtime_settings(db)
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
        if respect_enabled and not settings.daily_synthesis_enabled:
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
        provider_settings = settings.provider
        if not provider_settings.configured:
            report["ai_synthesis"] = _synthesis_state(
                previous, status_name="not_configured", source_hash=source_hash
            )
            snapshot.payload_json = report
            snapshot.updated_at = utcnow()
            return {
                "report_synthesis_status": "not_configured",
                "report_date": report_date.isoformat(),
            }
        budget_mode = settings.analysis_budget
        profile_id = profile.id
        report_id = snapshot.id

    generated = await generate_daily_report_synthesis(
        create_model_adapter(provider_settings),
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
        snapshot = db.execute(
            select(DailyResearchReport).where(DailyResearchReport.id == report_id)
        ).scalar_one()
        db.add(
            ReportSynthesisRun(
                profile_id=profile_id,
                report_id=report_id,
                report_date=report_date,
                source_hash=source_hash,
                provider=generated.provider,
                model=generated.model,
                transport=generated.transport,
                payload_json=synthesis,
                **usage.analysis_fields(budget_mode),
            )
        )
        payload = snapshot.payload
        payload["ai_synthesis"] = synthesis
        snapshot.payload_json = payload
        snapshot.updated_at = generated_at
    return {"report_synthesis_status": "ready", "report_date": report_date.isoformat()}


async def send_daily_report_email(
    report_date: date,
    *,
    delivery_kind: str = "automatic",
    require_enabled: bool = True,
) -> dict[str, Any]:
    with SessionLocal() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        if require_enabled and not email_enabled(settings):
            return {"email_status": "disabled", "email_sent": 0, "email_failed": 0}
        mail_settings = smtp_settings(settings)
        if not mail_settings.ready:
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
        recipients = mail_settings.recipients

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
            message = build_daily_message(mail_settings, report, recipient)
            await asyncio.to_thread(deliver_message, mail_settings, message)
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
    result.update(create_daily_report(report_date=pipeline_date, pipeline_status=result))
    current_report_date = date.fromisoformat(str(result["report_date"]))
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
