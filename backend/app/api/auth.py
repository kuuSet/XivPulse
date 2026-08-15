"""Server authentication HTTP boundary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..services.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    auth_config,
    clear_login_failures,
    issue_session,
    login_retry_after,
    record_login_failure,
    session_username,
    verify_password,
)

router = APIRouter()


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=10000)


def _source_ip(request: Request) -> str:
    config = auth_config()
    if config.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client is not None else "unknown"


async def require_server_auth(request: Request, call_next: Any) -> Response:
    config = auth_config()
    path = request.url.path
    public_api = {
        "/api/health",
        "/api/auth/session",
        "/api/auth/login",
        "/api/auth/logout",
    }
    if config.enabled and path.startswith("/api/") and path not in public_api:
        username = session_username(config, request.cookies.get(SESSION_COOKIE))
        if username is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        request.state.auth_user = username
    return await call_next(request)


@router.get("/api/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    config = auth_config()
    username = session_username(config, request.cookies.get(SESSION_COOKIE))
    return {
        "enabled": config.enabled,
        "authenticated": not config.enabled or username is not None,
        "username": username,
    }


@router.post("/api/auth/login")
def auth_login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    config = auth_config()
    if not config.enabled:
        return {"enabled": False, "authenticated": True, "username": "local"}
    source_ip = _source_ip(request)
    retry_after = login_retry_after(source_ip)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    if not verify_password(config, payload.username, payload.password):
        retry_after = record_login_failure(source_ip)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(status_code=401, detail="用户名或密码错误", headers=headers)
    clear_login_failures(source_ip)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(config),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )
    return {"enabled": True, "authenticated": True, "username": config.username}


@router.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    config = auth_config()
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=config.secure_cookies,
        samesite="lax",
        path="/",
    )
    return {"enabled": config.enabled, "authenticated": False}
