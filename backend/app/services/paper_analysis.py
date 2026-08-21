"""Run the title, abstract, and full-text LLM stages."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from ..arxiv.affiliations import fetch_affiliations
from ..arxiv.repository import PaperRepository
from ..database import SessionLocal, session_scope
from ..jobs.service import update_job_progress
from ..models import Analysis, Paper, PaperMatch, utcnow
from ..settings import load_runtime_settings
from .analyzer import PaperAnalyzer
from .provider import TokenUsage
from .provider_factory import create_model_adapter
from .state import active_profile


def _date_condition(report_date: date | None):
    if report_date is None:
        return PaperMatch.paper.has(Paper.last_seen_at >= datetime.now(UTC) - timedelta(days=7))
    start = datetime.combine(report_date, time.min, tzinfo=UTC)
    end = datetime.combine(report_date + timedelta(days=1), time.min, tzinfo=UTC)
    return PaperMatch.paper.has((Paper.published_at >= start) & (Paper.published_at < end))


async def analyze_papers(
    limit: int | None = None,
    *,
    job_id: str | None = None,
    report_date: date | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, Any]:
    with session_factory() as db:
        profile = active_profile(db)
        settings = load_runtime_settings(db)
        profile_id = profile.id
        topics = list(profile.topics)

    deep_limit = settings.deep_analysis_limit if limit is None else int(limit)
    batch_size = settings.prefilter_batch_size
    budget_mode = settings.analysis_budget
    analyzer = PaperAnalyzer(
        create_model_adapter(settings.providers.prefilter),
        create_model_adapter(settings.providers.screening),
        create_model_adapter(settings.providers.deep),
        budget_mode=budget_mode,
        prompt_template_id=settings.prompt_template_id,
        deep_analysis_prompt=settings.deep_analysis_prompt,
    )
    date_condition = _date_condition(report_date)

    with session_factory() as db:
        pending_prefilter = (
            db.scalars(
                select(PaperMatch)
                .options(joinedload(PaperMatch.paper))
                .where(
                    PaperMatch.profile_id == profile_id,
                    PaperMatch.prefiltered_at.is_(None),
                    date_condition,
                )
                .order_by(PaperMatch.id)
            )
            .unique()
            .all()
        )

    prefiltered = 0
    prefilter_kept = 0
    prefilter_rejected = 0
    prefilter_batches = 0
    prefilter_usage = TokenUsage()
    for start in range(0, len(pending_prefilter), batch_size):
        batch = pending_prefilter[start : start + batch_size]
        generated = await analyzer.prefilter_with_metadata(
            [match.paper for match in batch],
            topics,
        )
        decisions = {item.paper_id: item.decision for item in generated.data.items}
        timestamp = utcnow()
        with session_scope(session_factory) as db:
            for match in batch:
                stored = db.get(PaperMatch, match.id)
                stored.prefilter_decision = decisions[match.paper_id]
                stored.prefiltered_at = timestamp
        kept = sum(decisions[match.paper_id] != "irrelevant" for match in batch)
        prefiltered += len(batch)
        prefilter_kept += kept
        prefilter_rejected += len(batch) - kept
        prefilter_batches += 1
        prefilter_usage = prefilter_usage.plus(generated.usage)
        update_job_progress(
            job_id,
            progress=35,
            message=f"标题预筛 {prefiltered}/{len(pending_prefilter)}",
            details={
                "prefiltered": prefiltered,
                "prefilter_kept": prefilter_kept,
                "prefilter_rejected": prefilter_rejected,
            },
            session_factory=session_factory,
        )

    affiliations_enriched = 0
    if settings.watched_organizations:
        with session_factory() as db:
            arxiv_ids = list(
                db.scalars(
                    select(Paper.arxiv_id)
                    .join(PaperMatch, PaperMatch.paper_id == Paper.id)
                    .where(
                        PaperMatch.profile_id == profile_id,
                        PaperMatch.prefilter_decision.in_(("relevant", "uncertain")),
                        date_condition,
                    )
                ).all()
            )
        affiliations = await fetch_affiliations(arxiv_ids)
        with session_scope(session_factory) as db:
            PaperRepository(db).update_affiliations(affiliations)
        affiliations_enriched = len(affiliations)

    with session_factory() as db:
        pending_screening = (
            db.scalars(
                select(PaperMatch)
                .options(joinedload(PaperMatch.paper))
                .where(
                    PaperMatch.profile_id == profile_id,
                    PaperMatch.prefilter_decision.in_(("relevant", "uncertain")),
                    PaperMatch.screened_at.is_(None),
                    date_condition,
                )
                .order_by(PaperMatch.id)
            )
            .unique()
            .all()
        )

    screened = 0
    screening_usage = TokenUsage()
    for match in pending_screening:
        generated = await analyzer.screen_with_metadata(match.paper, topics)
        result = generated.data
        timestamp = utcnow()
        with session_scope(session_factory) as db:
            stored = db.get(PaperMatch, match.id)
            stored.matched_topics_json = []
            stored.screening_relevance_score = result.score
            stored.reason = ""
            stored.screened_at = timestamp
            db.add(
                Analysis(
                    paper_id=match.paper_id,
                    profile_id=profile_id,
                    kind="screening",
                    status="completed",
                    provider=generated.provider,
                    model=generated.model,
                    transport=generated.transport,
                    content_source="abstract",
                    relevance_score=result.score,
                    payload_json={
                        **result.model_dump(),
                        "_analysis_prompt": generated.prompt_metadata,
                    },
                    **generated.usage.analysis_fields(budget_mode),
                )
            )
        screened += 1
        screening_usage = screening_usage.plus(generated.usage)
        update_job_progress(
            job_id,
            progress=70,
            message=f"摘要粗排 {screened}/{len(pending_screening)}",
            details={"screened": screened, "screening_total": len(pending_screening)},
            session_factory=session_factory,
        )

    with session_factory() as db:
        deep_matches = (
            db.scalars(
                select(PaperMatch)
                .options(joinedload(PaperMatch.paper))
                .where(
                    PaperMatch.profile_id == profile_id,
                    PaperMatch.screening_relevance_score >= settings.deep_analysis_threshold,
                    PaperMatch.screened_at.is_not(None),
                    date_condition,
                    ~PaperMatch.paper.has(
                        Paper.analyses.any(
                            (Analysis.profile_id == profile_id)
                            & (Analysis.kind == "deep")
                            & (Analysis.status == "completed")
                        )
                    ),
                )
                .order_by(desc(PaperMatch.screening_relevance_score), PaperMatch.id)
                .limit(deep_limit)
            )
            .unique()
            .all()
        )

    analyzed = 0
    deep_usage = TokenUsage()
    for match in deep_matches:
        generated, fulltext = await analyzer.deep_analyze_with_metadata(match.paper, topics)
        result = generated.data
        payload = {
            **result.model_dump(),
            "_analysis_prompt": generated.prompt_metadata,
        }
        with session_scope(session_factory) as db:
            db.add(
                Analysis(
                    paper_id=match.paper_id,
                    profile_id=profile_id,
                    kind="deep",
                    status="completed",
                    provider=generated.provider,
                    model=generated.model,
                    transport=generated.transport,
                    content_source=fulltext.source if fulltext.content else "abstract",
                    summary=result.summary,
                    verdict=result.verdict,
                    relevance_score=result.relevance_score,
                    novelty_score=result.novelty_score,
                    rigor_score=result.rigor_score,
                    impact_score=result.impact_score,
                    strengths_json=result.strengths,
                    weaknesses_json=result.weaknesses,
                    tags_json=result.tags,
                    payload_json=payload,
                    **generated.usage.analysis_fields(budget_mode),
                )
            )
        analyzed += 1
        deep_usage = deep_usage.plus(generated.usage)

    return {
        "prefiltered": prefiltered,
        "prefilter_kept": prefilter_kept,
        "prefilter_rejected": prefilter_rejected,
        "prefilter_batches": prefilter_batches,
        "prefilter_usage": prefilter_usage.analysis_fields(budget_mode),
        "screened": screened,
        "screening_total": len(pending_screening),
        "screening_usage": screening_usage.analysis_fields(budget_mode),
        "analyzed": analyzed,
        "eligible": len(deep_matches),
        "deep_usage": deep_usage.analysis_fields(budget_mode),
        "threshold": settings.deep_analysis_threshold,
        "affiliations_enriched": affiliations_enriched,
    }
