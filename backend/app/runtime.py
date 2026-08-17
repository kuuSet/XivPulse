"""Process initialization shared by web and scheduler entrypoints."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .database import init_db, session_scope
from .models import Job, Paper, PaperMatch, utcnow
from .services.auth import auth_settings
from .services.directions.matching import canonical_requested_direction_names
from .services.jobs import RUNNING_STATUSES
from .services.semantic.embedding import MODEL_ID as SEMANTIC_MODEL_ID
from .services.semantic.embedding import match_papers as semantic_match_papers
from .services.state import active_profile
from .settings import get_process_settings, set_setting

LOG = logging.getLogger("xivpulse")
_initialization_lock = threading.Lock()
_initialized = False
_embedding_backfill_started = False


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _backfill_existing_semantic_embeddings() -> None:
    """Populate local vectors for existing papers without invoking AI analysis."""

    try:
        with session_scope() as db:
            profile = active_profile(db)
            paper_ids = list(db.scalars(select(Paper.id).order_by(Paper.id)).all())
            profile_id = profile.id
        total = len(paper_ids)
        processed = 0
        for start in range(0, total, 128):
            selected_ids = paper_ids[start : start + 128]
            with session_scope() as db:
                profile = active_profile(db)
                topics = list(profile.topics)
                papers = list(
                    db.scalars(select(Paper).where(Paper.id.in_(selected_ids))).all()
                )
                results = semantic_match_papers(db, papers, topics)
                matches = db.scalars(
                    select(PaperMatch).where(
                        PaperMatch.profile_id == profile_id,
                        PaperMatch.paper_id.in_(selected_ids),
                    )
                ).all()
                for match in matches:
                    semantic = results.get(match.paper_id)
                    if semantic is not None:
                        match.semantic_score = semantic.score
                        match.semantic_topics_json = semantic.topic_scores
                        match.semantic_model = SEMANTIC_MODEL_ID
                    match.matched_topics_json = canonical_requested_direction_names(
                        match.matched_topics,
                        topics,
                        relevant=True,
                    )
                processed += len(selected_ids)
                set_setting(
                    db,
                    "semantic_backfill_status",
                    {"status": "running", "processed": processed, "total": total},
                )
        with session_scope() as db:
            set_setting(
                db,
                "semantic_backfill_status",
                {"status": "completed", "processed": processed, "total": total},
            )
    except Exception as exc:
        LOG.exception("Existing-paper semantic embedding backfill failed")
        try:
            with session_scope() as db:
                set_setting(
                    db,
                    "semantic_backfill_status",
                    {"status": "failed", "error": str(exc)[:1000]},
                )
        except Exception:
            LOG.exception("Failed to persist embedding backfill error")


def initialize_application() -> None:
    global _embedding_backfill_started, _initialized
    if _initialized:
        return
    with _initialization_lock:
        if _initialized:
            return
        auth_settings()
        init_db()
        with session_scope() as db:
            stale_before = utcnow() - timedelta(hours=2)
            for job in db.scalars(select(Job).where(Job.status.in_(RUNNING_STATUSES))).all():
                heartbeat = _as_utc(job.started_at or job.created_at)
                if heartbeat and heartbeat < stale_before:
                    job.status = "failed"
                    job.error = "应用退出后遗留的任务已自动关闭"
                    job.message = "任务超时"
                    job.completed_at = utcnow()
        _initialized = True
        if (
            get_process_settings().semantic_backfill_enabled
            and not _embedding_backfill_started
        ):
            _embedding_backfill_started = True
            threading.Thread(
                target=_backfill_existing_semantic_embeddings,
                name="semantic-embedding-backfill",
                daemon=True,
            ).start()
