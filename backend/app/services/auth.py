"""Single-user authentication primitives for server deployments."""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..env import xivpulse_env

SESSION_COOKIE = "xivpulse_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60
FAILURE_LIMIT = 5
FAILURE_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    username: str
    password_hash: str
    session_secret: str
    secure_cookies: bool
    trust_proxy: bool

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.username:
            raise RuntimeError("服务器认证已启用，但未配置 XIVPULSE_ADMIN_USERNAME")
        if not self.password_hash.startswith("$argon2"):
            raise RuntimeError("XIVPULSE_ADMIN_PASSWORD_HASH 必须是 Argon2 哈希")
        if len(self.session_secret.encode("utf-8")) < 32:
            raise RuntimeError("XIVPULSE_SESSION_SECRET 至少需要 32 字节")


def auth_config() -> AuthConfig:
    enabled = _enabled(xivpulse_env("AUTH_ENABLED"))
    config = AuthConfig(
        enabled=enabled,
        username=str(xivpulse_env("ADMIN_USERNAME", "")).strip(),
        password_hash=str(xivpulse_env("ADMIN_PASSWORD_HASH", "")).strip(),
        session_secret=str(xivpulse_env("SESSION_SECRET", "")),
        secure_cookies=_enabled(
            xivpulse_env("SECURE_COOKIES", "true" if enabled else "false")
        ),
        trust_proxy=_enabled(xivpulse_env("TRUST_PROXY")),
    )
    config.validate()
    return config


def _serializer(config: AuthConfig) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt="xivpulse-session-v1")


def issue_session(config: AuthConfig) -> str:
    return _serializer(config).dumps(
        {"username": config.username, "issued_at": datetime.now(UTC).isoformat()}
    )


def session_username(config: AuthConfig, token: str | None) -> str | None:
    if not config.enabled:
        return config.username or "local"
    if not token:
        return None
    try:
        payload = _serializer(config).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    username = str(payload.get("username") or "") if isinstance(payload, dict) else ""
    return username if username == config.username else None


def verify_password(config: AuthConfig, username: str, password: str) -> bool:
    if not config.enabled:
        return True
    if username != config.username:
        return False
    try:
        return PasswordHasher().verify(config.password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


_failure_lock = threading.Lock()
_failures: dict[str, deque[datetime]] = defaultdict(deque)
_locked_until: dict[str, datetime] = {}


def login_retry_after(source_ip: str) -> int:
    now = datetime.now(UTC)
    with _failure_lock:
        lock_until = _locked_until.get(source_ip)
        if lock_until is None or lock_until <= now:
            _locked_until.pop(source_ip, None)
            return 0
        return max(1, int((lock_until - now).total_seconds()))


def record_login_failure(source_ip: str) -> int:
    now = datetime.now(UTC)
    with _failure_lock:
        history = _failures[source_ip]
        cutoff = now - FAILURE_WINDOW
        while history and history[0] < cutoff:
            history.popleft()
        history.append(now)
        if len(history) >= FAILURE_LIMIT:
            _locked_until[source_ip] = now + LOCK_DURATION
            history.clear()
            return int(LOCK_DURATION.total_seconds())
    return 0


def clear_login_failures(source_ip: str) -> None:
    with _failure_lock:
        _failures.pop(source_ip, None)
        _locked_until.pop(source_ip, None)


__all__ = [
    "SESSION_COOKIE",
    "SESSION_MAX_AGE",
    "AuthConfig",
    "auth_config",
    "clear_login_failures",
    "issue_session",
    "login_retry_after",
    "record_login_failure",
    "session_username",
    "verify_password",
]
