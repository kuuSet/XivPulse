"""Production pipeline composition shared by process entrypoints."""

from ..runtime import initialize_application
from ..services.paper_analysis import analyze_papers
from ..services.paper_sync import sync_papers
from ..services.reporting.service import finalize_daily_stage
from .service import PipelineCoordinator

daily_pipeline = PipelineCoordinator(
    initialize=initialize_application,
    sync_stage=sync_papers,
    analyze_stage=analyze_papers,
    finalize_daily_stage=finalize_daily_stage,
)

__all__ = ["daily_pipeline"]
