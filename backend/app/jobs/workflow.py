"""Production pipeline composition shared by process entrypoints."""

from functools import partial

from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal
from ..modules.analysis.pipeline import analyze_papers
from ..services.paper_sync import sync_papers
from ..services.reporting.service import finalize_daily_stage
from .service import PipelineCoordinator, update_job_progress


def build_pipeline(
    session_factory: sessionmaker[Session] = SessionLocal,
) -> PipelineCoordinator:
    return PipelineCoordinator(
        sync_stage=partial(sync_papers, session_factory=session_factory),
        analyze_stage=partial(
            analyze_papers,
            session_factory=session_factory,
            progress_callback=partial(
                update_job_progress,
                session_factory=session_factory,
            ),
        ),
        finalize_daily_stage=partial(
            finalize_daily_stage,
            session_factory=session_factory,
        ),
        session_factory=session_factory,
    )


__all__ = ["build_pipeline"]
