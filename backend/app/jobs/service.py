"""Pipeline stages, persisted job lifecycle, and cross-process locking."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from ..database import SessionLocal, session_scope
from ..models import Job, utcnow
from ..settings import get_process_settings

LOG = logging.getLogger("xivpulse.jobs")
RUNNING_STATUSES = ("queued", "running")
STALE_JOB_AGE = timedelta(hours=2)
DEFAULT_LOCK_PATH = get_process_settings().data_dir / "pipeline.lock"

PipelineKind = Literal["daily", "sync", "analyze"]
PipelineResult = dict[str, Any]
SyncStage = Callable[..., Awaitable[PipelineResult]]
AnalyzeStage = Callable[..., Awaitable[PipelineResult]]
FinalizeDailyStage = Callable[[date, PipelineResult, str | None], Awaitable[PipelineResult]]


class JobConflict(RuntimeError):
    """Raised when another process already owns the pipeline."""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def recover_stale_jobs(db: Session) -> None:
    stale_before = utcnow() - STALE_JOB_AGE
    jobs = db.scalars(select(Job).where(Job.status.in_(RUNNING_STATUSES))).all()
    recovered = False
    for job in jobs:
        heartbeat = _as_utc(job.started_at or job.created_at)
        if heartbeat is not None and heartbeat < stale_before:
            job.status = "failed"
            job.error = "应用退出后遗留的任务已自动关闭"
            job.message = "任务超时"
            job.completed_at = utcnow()
            recovered = True
    if recovered:
        db.flush()


def update_job_progress(
    job_id: str | None,
    *,
    progress: int,
    message: str,
    details: dict[str, Any] | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    if job_id is None:
        return
    with session_scope(session_factory) as db:
        job = db.get(Job, job_id)
        if job is not None and job.status in RUNNING_STATUSES:
            job.progress = max(0, min(100, progress))
            job.message = message
            if details:
                job.result_json = {**job.result, **details}


class PipelineCoordinator:
    def __init__(
        self,
        *,
        initialize: Callable[[], None],
        sync_stage: SyncStage,
        analyze_stage: AnalyzeStage,
        finalize_daily_stage: FinalizeDailyStage,
        session_factory: sessionmaker[Session] = SessionLocal,
        lock_path: Path = DEFAULT_LOCK_PATH,
    ) -> None:
        self._initialize = initialize
        self._sync_stage = sync_stage
        self._analyze_stage = analyze_stage
        self._finalize_daily_stage = finalize_daily_stage
        self._session_factory = session_factory
        self._lock_path = lock_path
        self._job_lock = threading.Lock()
        self._active_job_id: str | None = None
        self._tasks: set[asyncio.Task[PipelineResult]] = set()

    async def execute(
        self,
        kind: PipelineKind,
        *,
        job_id: str | None = None,
        target_date: date | None = None,
    ) -> PipelineResult:
        result: PipelineResult = {}
        pipeline_date: date | None = None
        if kind in ("daily", "sync"):
            update_job_progress(
                job_id,
                progress=15,
                message="正在抓取 arXiv 论文",
                session_factory=self._session_factory,
            )
            result.update(await self._sync_stage(target_date=target_date))
            pipeline_date = date.fromisoformat(str(result["fetch_date"]))
        if kind in ("daily", "analyze"):
            update_job_progress(
                job_id,
                progress=40,
                message="正在进行标题预筛、摘要粗排与全文深读",
                session_factory=self._session_factory,
            )
            result.update(await self._analyze_stage(job_id=job_id, report_date=pipeline_date))
        if kind == "daily":
            if pipeline_date is None:
                raise RuntimeError("Daily pipeline is missing its fetch date")
            result = await self._finalize_daily_stage(pipeline_date, result, job_id)
        return result

    def start_job(self, kind: PipelineKind, *, target_date: date | None = None) -> PipelineResult:
        job_id = self._create_job(kind, target_date)
        task = asyncio.create_task(self._run_job(job_id, kind, target_date=target_date))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {
            "job_id": job_id,
            "status": "queued",
            "message": "任务已在后台启动",
            "date": target_date.isoformat() if target_date else None,
        }

    async def run_daily(
        self, trigger: str = "manual", *, target_date: date | None = None
    ) -> PipelineResult:
        try:
            job_id = self._create_job("daily", target_date)
        except JobConflict:
            return {"accepted": False, "success": False, "message": "已有任务正在运行"}
        result = await self._run_job(job_id, "daily", target_date=target_date, trigger=trigger)
        return {"accepted": True, **result}

    def _create_job(self, kind: PipelineKind, target_date: date | None) -> str:
        self._initialize()
        with self._job_lock:
            if self._active_job_id is not None:
                raise JobConflict("已有抓取或分析任务正在运行")
            job_id = str(uuid4())
            if not self._acquire_file_lock(job_id):
                raise JobConflict("已有抓取或分析任务正在运行")
            try:
                with session_scope(self._session_factory) as db:
                    recover_stale_jobs(db)
                    existing = db.scalar(
                        select(Job)
                        .where(Job.status.in_(RUNNING_STATUSES))
                        .order_by(desc(Job.created_at))
                    )
                    if existing is not None:
                        raise JobConflict("已有抓取或分析任务正在运行")
                    db.add(
                        Job(
                            id=job_id,
                            kind=kind,
                            status="queued",
                            message="任务已排队",
                            result_json=(
                                {"fetch_date": target_date.isoformat()} if target_date else {}
                            ),
                        )
                    )
            except Exception:
                self._release_file_lock(job_id)
                raise
            self._active_job_id = job_id
            return job_id

    async def _run_job(
        self,
        job_id: str,
        kind: PipelineKind,
        *,
        target_date: date | None = None,
        trigger: str | None = None,
    ) -> PipelineResult:
        scheduled = trigger is not None
        try:
            with session_scope(self._session_factory) as db:
                job = db.get(Job, job_id)
                if job is None:
                    raise RuntimeError(f"Job {job_id} no longer exists")
                job.status = "running"
                job.started_at = utcnow()
                job.message = f"计划任务执行中（{trigger}）" if scheduled else "任务执行中"
                job.progress = 5

            result = await self.execute(kind, job_id=job_id, target_date=target_date)
            with session_scope(self._session_factory) as db:
                job = db.get(Job, job_id)
                if job is None:
                    raise RuntimeError(f"Job {job_id} no longer exists")
                job.status = "completed"
                job.progress = 100
                job.result_json = result
                job.message = "计划任务已完成" if scheduled else "任务已完成"
                job.completed_at = utcnow()
            return {"success": True, **result}
        except Exception as exc:
            LOG.exception("Pipeline job %s failed", job_id)
            with session_scope(self._session_factory) as db:
                job = db.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.error = str(exc)
                    job.message = "计划任务失败" if scheduled else "任务失败"
                    job.completed_at = utcnow()
            return {"success": False, "error": str(exc)}
        finally:
            with self._job_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
            self._release_file_lock(job_id)

    def _acquire_file_lock(self, token: str) -> bool:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = datetime.now().timestamp() - self._lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age <= STALE_JOB_AGE.total_seconds():
                    return False
                self._lock_path.unlink()
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(f"{token}\n{os.getpid()}\n{socket.gethostname()}\n")
            return True
        return False

    def _release_file_lock(self, token: str) -> None:
        try:
            owner = self._lock_path.read_text(encoding="utf-8").splitlines()[0]
        except FileNotFoundError:
            return
        if owner == token:
            self._lock_path.unlink()


__all__ = [
    "JobConflict",
    "PipelineCoordinator",
    "PipelineKind",
    "RUNNING_STATUSES",
    "recover_stale_jobs",
    "update_job_progress",
]
