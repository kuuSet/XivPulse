"""FastAPI application and orchestration for the local paper radar."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.auth import require_server_auth
from .api.auth import router as auth_router
from .api.dashboard import router as dashboard_router
from .api.jobs import router as jobs_router
from .api.papers import router as papers_router
from .api.projects import router as projects_router
from .api.reports import router as reports_router
from .api.settings import router as settings_router
from .settings import get_process_settings
from .runtime import initialize_application

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_application()
    yield


app = FastAPI(
    title="XivPulse",
    description="本机 arXiv 论文筛选、总结与推荐服务",
    version="0.1.0",
    lifespan=lifespan,
)

allowed_hosts = list(get_process_settings().server_allowed_hosts)
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

app.middleware("http")(require_server_auth)
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(jobs_router)
app.include_router(papers_router)
app.include_router(projects_router)
app.include_router(reports_router)
app.include_router(settings_router)


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        requested = (FRONTEND_DIST / path).resolve()
        try:
            requested.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            raise HTTPException(status_code=404) from None
        if path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def frontend_fallback() -> str:
        return """<!doctype html>
        <meta charset="utf-8"><title>XivPulse</title>
        <style>
        body { font-family: system-ui; max-width: 720px; margin: 80px auto; padding: 24px; }
        code { background: #eef1f4; padding: 2px 6px; }
        </style>
        <h1>XivPulse 后端已启动</h1>
        <p>尚未找到前端构建文件。请重新运行 <code>.\\start.ps1</code>，或进入
        <code>frontend</code> 执行 <code>npm install</code> 和 <code>npm run build</code>。</p>
        <p><a href="/docs">打开 API 文档</a></p>"""


__all__ = ["app"]
