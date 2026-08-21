"""Process initialization shared by web and scheduler entrypoints."""

from __future__ import annotations

import threading

from .database import init_db, session_scope
from .jobs.service import recover_stale_jobs
from .services.auth import auth_settings

_initialization_lock = threading.Lock()
_initialized = False


def initialize_application() -> None:
    global _initialized
    if _initialized:
        return
    with _initialization_lock:
        if _initialized:
            return
        auth_settings()
        init_db()
        with session_scope() as db:
            recover_stale_jobs(db)
        _initialized = True
