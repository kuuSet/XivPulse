"""Frontend static files and single-page application fallback."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def register_frontend(app: FastAPI, dist: Path) -> None:
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> Response:
            requested = (dist / path).resolve()
            try:
                requested.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404) from None
            if path and requested.is_file():
                return FileResponse(requested)
            return FileResponse(dist / "index.html")

        return

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


__all__ = ["register_frontend"]
