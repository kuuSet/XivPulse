"""Application workflow composition shared by process entrypoints."""

from .pipeline import PipelineCoordinator
from .runtime import initialize_application
from .services.reporting.service import finalize_daily_stage
from .services.paper_analysis import analyze_papers
from .services.paper_sync import sync_papers

daily_pipeline = PipelineCoordinator(
    initialize=initialize_application,
    sync_stage=sync_papers,
    analyze_stage=analyze_papers,
    finalize_daily_stage=finalize_daily_stage,
)
