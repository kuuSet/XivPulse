"""Screen queued papers and run bounded deep analysis."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import joinedload

from ..settings import load_runtime_settings
from ..database import SessionLocal, session_scope
from ..models import Analysis, Paper, PaperMatch, Profile, ScreeningQueueItem, utcnow
from .analytics import current_date_in_timezone
from .analyzer import PaperAnalyzer
from .direction_matching import canonical_requested_direction_names
from .direction_taxonomy import stamp_direction_attribution
from .jobs import update_job_progress
from .prompt_catalog import resolve_prompt_settings
from .provider import OpenAICompatibleAdapter, ProviderError
from .semantic import is_candidate as is_semantic_candidate
from .semantic import recall_mode
from .state import active_profile

LOG = logging.getLogger("xivpulse")
SCREENING_RETRY_DELAYS = (5.0, 30.0)


async def analyze_papers(
    limit: int | None = None,
    *,
    job_id: str | None = None,
    report_date: date | None = None,
) -> dict[str, Any]:
    with SessionLocal() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        limit = int(limit if limit is not None else settings.deep_analysis_limit)
        threshold = settings.deep_analysis_threshold
        screening_batch_size = max(1, settings.screening_batch_size)
        selected_recall_mode = recall_mode(settings.semantic_recall_mode)
        today = report_date or current_date_in_timezone(settings.schedule_timezone)
        recent_matches = (
            db.scalars(
                select(PaperMatch)
                .options(
                    joinedload(PaperMatch.paper),
                    joinedload(PaperMatch.profile).selectinload(Profile.topics),
                )
                .where(
                    PaperMatch.profile_id == profile.id,
                    PaperMatch.screened_at.is_(None),
                    PaperMatch.paper.has(
                        Paper.last_seen_at >= datetime.now(UTC) - timedelta(days=7)
                    ),
                )
            )
            .unique()
            .all()
        )
        watched_authors = {value.casefold() for value in settings.watched_authors}
        existing_queue_keys = set(
            db.scalars(
                select(ScreeningQueueItem.paper_id).where(
                    ScreeningQueueItem.profile_id == profile.id
                )
            ).all()
        )
        for match in recent_matches:
            watched = any(
                watched_name in author.casefold()
                for author in match.paper.authors
                for watched_name in watched_authors
            )
            if match.paper_id in existing_queue_keys or not is_semantic_candidate(
                semantic_score=match.semantic_score,
                mode=selected_recall_mode,
                watched_author=watched,
            ):
                continue
            db.add(
                ScreeningQueueItem(
                    paper_id=match.paper_id,
                    profile_id=profile.id,
                    report_date=today,
                    recall_mode=selected_recall_mode,
                    semantic_score=match.semantic_score,
                    semantic_topics_json=dict(match.semantic_topics_json or {}),
                )
            )
        queue_items = db.scalars(
            select(ScreeningQueueItem).where(ScreeningQueueItem.profile_id == profile.id)
        ).all()
        for item in queue_items:
            match = db.scalar(
                select(PaperMatch).where(
                    PaperMatch.paper_id == item.paper_id,
                    PaperMatch.profile_id == profile.id,
                )
            )
            if match is not None and match.screened_at is not None:
                item.status = "completed"
                item.completed_at = item.completed_at or match.screened_at
                item.last_error = None
            elif item.status in {"failed", "processing"}:
                item.status = "queued"
                item.attempts = 0
                item.last_error = None
                item.completed_at = None
        db.commit()
    provider_settings = settings.provider
    adapter = OpenAICompatibleAdapter(settings=provider_settings)
    budget_mode = settings.analysis_budget
    prompt_settings = resolve_prompt_settings(settings.prompt_values())
    analyzer = PaperAnalyzer(
        adapter,
        budget_mode=budget_mode,
        prompt_template_id=prompt_settings["prompt_template_id"],
        screening_prompt=prompt_settings["screening_prompt"],
        deep_analysis_prompt=prompt_settings["deep_analysis_prompt"],
    )

    screened = 0
    screening_errors: list[str] = []
    screening_failed = 0
    screening_batches = 0
    screening_report_dates: set[date] = set()
    with SessionLocal() as db:
        profile = active_profile(db)
        screening_queue_total = int(
            db.scalar(
                select(func.count(ScreeningQueueItem.id)).where(
                    ScreeningQueueItem.profile_id == profile.id,
                    ScreeningQueueItem.status.in_(("queued", "processing", "failed")),
                )
            )
            or 0
        )
    total_batches = (
        (screening_queue_total + screening_batch_size - 1) // screening_batch_size
        if screening_queue_total
        else 0
    )

    async def screen_one(
        queue_id: int,
        match: PaperMatch,
    ) -> tuple[int, PaperMatch, Any | None, Exception | None, int]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                generated = await analyzer.screen_with_metadata(match.paper, match.profile)
                return queue_id, match, generated, None, attempt
            except ProviderError as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(SCREENING_RETRY_DELAYS[attempt - 1])
        return queue_id, match, None, last_error, 3

    while True:
        with session_scope() as db:
            profile = active_profile(db)
            queue_batch = (
                db.scalars(
                    select(ScreeningQueueItem)
                    .options(
                        joinedload(ScreeningQueueItem.paper),
                        joinedload(ScreeningQueueItem.profile).selectinload(Profile.topics),
                    )
                    .where(
                        ScreeningQueueItem.profile_id == profile.id,
                        ScreeningQueueItem.status == "queued",
                    )
                    .order_by(
                        ScreeningQueueItem.report_date,
                        desc(ScreeningQueueItem.semantic_score),
                        ScreeningQueueItem.id,
                    )
                    .limit(screening_batch_size)
                )
                .unique()
                .all()
            )
            for item in queue_batch:
                item.status = "processing"
                screening_report_dates.add(item.report_date)
            batch_payload = []
            for item in queue_batch:
                match = db.scalar(
                    select(PaperMatch)
                    .options(
                        joinedload(PaperMatch.paper),
                        joinedload(PaperMatch.profile).selectinload(Profile.topics),
                    )
                    .where(
                        PaperMatch.paper_id == item.paper_id,
                        PaperMatch.profile_id == item.profile_id,
                    )
                )
                if match is None:
                    item.status = "failed"
                    item.attempts = 3
                    item.last_error = "PaperMatch no longer exists"
                    continue
                batch_payload.append((item.id, match))
        if not queue_batch:
            break
        screening_batches += 1
        batch_results: list[tuple[int, PaperMatch, Any | None, Exception | None, int]] = []
        for batch_start in range(0, len(batch_payload), 4):
            batch_results.extend(
                await asyncio.gather(
                    *(
                        screen_one(queue_id, match)
                        for queue_id, match in batch_payload[batch_start : batch_start + 4]
                    )
                )
            )
        for queue_id, match, generated, error, attempts in batch_results:
            if error is not None or generated is None:
                LOG.error("Screening failed for paper %s: %s", match.paper_id, error)
                screening_errors.append(f"{match.paper_id}: {error}")
                screening_failed += 1
                with session_scope() as db:
                    item = db.get(ScreeningQueueItem, queue_id)
                    if item is not None:
                        item.status = "failed"
                        item.attempts = attempts
                        item.last_error = str(error or "Unknown screening error")
                        item.completed_at = None
                continue
            result = generated.data
            with session_scope() as db:
                stored_match = db.get(PaperMatch, match.id)
                if stored_match is None:
                    continue
                stored_match.relevance_score = result.relevance_score
                stored_match.screened_at = utcnow()
                ai_directions = canonical_requested_direction_names(
                    result.matched_topics,
                    list(match.profile.topics),
                    relevant=result.relevant is True,
                )
                stored_match.matched_topics_json = ai_directions
                stored_match.reason = result.reason
                item = db.get(ScreeningQueueItem, queue_id)
                if item is not None:
                    item.status = "completed"
                    item.attempts = attempts
                    item.last_error = None
                    item.completed_at = stored_match.screened_at
                db.add(
                    Analysis(
                        paper_id=match.paper_id,
                        profile_id=match.profile_id,
                        kind="screening",
                        status="completed",
                        provider=provider_settings.provider_name,
                        model=provider_settings.model_name,
                        transport=generated.transport,
                        content_source="abstract",
                        summary=result.reason,
                        verdict="relevant" if result.relevant else "not_relevant",
                        relevance_score=result.relevance_score,
                        tags_json=result.tags,
                        payload_json={
                            **stamp_direction_attribution(
                                result.model_dump(), list(match.profile.topics)
                            ),
                            "_analysis_prompt": generated.prompt_metadata,
                        },
                        **generated.usage.analysis_fields(budget_mode),
                    )
                )
            screened += 1
        completed_or_failed = screened + screening_failed
        progress = 35 + int(42 * completed_or_failed / max(1, screening_queue_total))
        update_job_progress(
            job_id,
            progress=progress,
            message=(
                f"摘要初筛第 {screening_batches}/{max(total_batches, screening_batches)} 批："
                f"成功 {screened}，失败 {screening_failed}，总候选 {screening_queue_total}"
            ),
            details={
                "screening_queue_total": screening_queue_total,
                "screening_completed": screened,
                "screening_failed": screening_failed,
                "screening_current_batch": screening_batches,
                "screening_total_batches": max(total_batches, screening_batches),
                "report_incomplete": screening_failed > 0,
            },
        )

    if screening_report_dates:
        with SessionLocal() as db:
            screening_failed = int(
                db.scalar(
                    select(func.count(ScreeningQueueItem.id)).where(
                        ScreeningQueueItem.profile_id == profile.id,
                        ScreeningQueueItem.report_date.in_(screening_report_dates),
                        ScreeningQueueItem.status == "failed",
                    )
                )
                or 0
            )

    with SessionLocal() as db:
        profile = active_profile(db)
        matches = (
            db.scalars(
                select(PaperMatch)
                .options(
                    joinedload(PaperMatch.paper),
                    joinedload(PaperMatch.profile).selectinload(Profile.topics),
                )
                .where(
                    PaperMatch.profile_id == profile.id,
                    PaperMatch.relevance_score >= threshold,
                    PaperMatch.screened_at.is_not(None),
                    PaperMatch.paper.has(
                        Paper.last_seen_at >= datetime.now(UTC) - timedelta(days=7)
                    ),
                    ~PaperMatch.paper.has(
                        Paper.analyses.any(
                            (Analysis.profile_id == profile.id)
                            & (Analysis.kind == "deep")
                            & (Analysis.status == "completed")
                        )
                    ),
                )
                .order_by(desc(PaperMatch.relevance_score), desc(PaperMatch.semantic_score))
                .limit(max(0, limit))
            )
            .unique()
            .all()
        )
    count = 0
    errors: list[str] = []
    for match in matches:
        try:
            generated, fulltext = await analyzer.deep_analyze_with_metadata(
                match.paper, match.profile
            )
        except ProviderError as exc:
            LOG.warning("Deep analysis failed for paper %s: %s", match.paper_id, exc)
            errors.append(f"{match.paper_id}: {exc}")
            continue
        result = generated.data
        payload = result.model_dump()
        payload["_analysis_prompt"] = generated.prompt_metadata
        with session_scope() as db:
            db.add(
                Analysis(
                    paper_id=match.paper_id,
                    profile_id=match.profile_id,
                    kind="deep",
                    status="completed",
                    provider=provider_settings.provider_name,
                    model=provider_settings.model_name,
                    transport=generated.transport,
                    content_source=fulltext.source if fulltext.content else "abstract",
                    summary=payload.get("summary", ""),
                    verdict=payload.get("verdict", ""),
                    relevance_score=payload.get("relevance_score"),
                    novelty_score=payload.get("novelty_score"),
                    rigor_score=payload.get("rigor_score"),
                    impact_score=payload.get("impact_score"),
                    strengths_json=payload.get("strengths", []),
                    weaknesses_json=payload.get("weaknesses", []),
                    tags_json=payload.get("tags", []),
                    payload_json=payload,
                    **generated.usage.analysis_fields(budget_mode),
                )
            )
            stored_match = db.scalar(select(PaperMatch).where(PaperMatch.id == match.id))
            if stored_match is not None:
                stored_match.relevance_score = result.relevance_score
        count += 1
    return {
        "analyzed": count,
        "screened": screened,
        "screening_queue_total": screening_queue_total,
        "screening_completed": screened,
        "screening_failed": screening_failed,
        "screening_batches": screening_batches,
        "screening_total_batches": max(total_batches, screening_batches),
        "report_incomplete": screening_failed > 0,
        "screening_report_dates": sorted(value.isoformat() for value in screening_report_dates),
        "eligible": len(matches),
        "threshold": threshold,
        "screening_errors": screening_errors[:10],
        "analysis_errors": errors[:10],
    }
