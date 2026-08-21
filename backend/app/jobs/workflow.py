"""Production pipeline composition shared by process entrypoints."""

from collections.abc import Callable
from functools import partial

from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal
from ..runtime import initialize_application
from ..services.paper_analysis import analyze_papers
from ..services.paper_sync import sync_papers
from ..services.reporting.service import finalize_daily_stage
from .service import PipelineCoordinator


def build_pipeline(
    session_factory: sessionmaker[Session] = SessionLocal,
    *,
    initialize: Callable[[], None] = initialize_application,
) -> PipelineCoordinator:
    return PipelineCoordinator(
        initialize=initialize,
        sync_stage=partial(sync_papers, session_factory=session_factory),
        analyze_stage=partial(analyze_papers, session_factory=session_factory),
        finalize_daily_stage=partial(
            finalize_daily_stage,
            session_factory=session_factory,
        ),
        session_factory=session_factory,
    )


daily_pipeline = build_pipeline()

__all__ = ["build_pipeline", "daily_pipeline"]
