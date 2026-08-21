"""XivPulse application factory."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import router as api_router
from .api.auth import require_server_auth
from .database import SessionLocal
from .jobs.service import PipelineCoordinator
from .jobs.workflow import build_pipeline
from .runtime import initialize_application
from .settings import get_process_settings
from .web import register_frontend

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def create_app(
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    pipeline: PipelineCoordinator | None = None,
    startup: Callable[[], None] | None = None,
    frontend_dist: Path | None = FRONTEND_DIST,
) -> FastAPI:
    initialize = (
        startup if startup is not None else partial(initialize_application, session_factory)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize()
        yield

    app = FastAPI(
        title="XivPulse",
        description="本机 arXiv 论文筛选、总结与推荐服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.pipeline = pipeline if pipeline is not None else build_pipeline(session_factory)

    allowed_hosts = list(get_process_settings().server_allowed_hosts)
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    app.middleware("http")(require_server_auth)
    app.include_router(api_router)
    if frontend_dist is not None:
        register_frontend(app, frontend_dist)
    return app


__all__ = ["create_app"]
