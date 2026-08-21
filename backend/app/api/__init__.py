"""HTTP API composition."""

from fastapi import APIRouter

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .interests import router as interests_router
from .jobs import router as jobs_router
from .methods import router as methods_router
from .papers import router as papers_router
from .projects import router as projects_router
from .reports import router as reports_router
from .settings import router as settings_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(jobs_router)
router.include_router(methods_router)
router.include_router(papers_router)
router.include_router(interests_router)
router.include_router(projects_router)
router.include_router(reports_router)
router.include_router(settings_router)

__all__ = ["router"]
