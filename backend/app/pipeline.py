"""Daily pipeline orchestration and job lifecycle management."""

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

from fastapi import HTTPException
from sqlalchemy import desc, select

from .database import session_scope
from .models import Job, utcnow
from .services.jobs import RUNNING_STATUSES, update_job_progress

LOG = logging.getLogger("xivpulse")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_LOCK_PATH = PROJECT_ROOT / "data" / "pipeline.lock"
PipelineKind = Literal["daily", "sync", "analyze"]
PipelineResult = dict[str, Any]
SyncStage = Callable[[], Awaitable[PipelineResult]]
AnalyzeStage = Callable[..., Awaitable[PipelineResult]]
FinalizeDailyStage = Callable[[date, PipelineResult, str | None], Awaitable[PipelineResult]]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _acquire_pipeline_lock(token: str) -> bool:
    """Atomically coordinate web and scheduled jobs across local processes."""

    PIPELINE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(
                PIPELINE_LOCK_PATH,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            try:
                age = datetime.now().timestamp() - PIPELINE_LOCK_PATH.stat().st_mtime
                if age <= timedelta(hours=2).total_seconds():
                    return False
                PIPELINE_LOCK_PATH.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{token}\n{os.getpid()}\n{socket.gethostname()}\n")
        return True
    return False


def _release_pipeline_lock(token: str) -> None:
    try:
        owner = PIPELINE_LOCK_PATH.read_text(encoding="utf-8").splitlines()[0]
        if owner == token:
            PIPELINE_LOCK_PATH.unlink()
    except (FileNotFoundError, OSError, UnicodeError):
        return


class PipelineCoordinator:
    """Run pipeline stages and persist their job state."""

    def __init__(
        self,
        *,
        initialize: Callable[[], None],
        sync_stage: SyncStage,
        analyze_stage: AnalyzeStage,
        finalize_daily_stage: FinalizeDailyStage,
    ) -> None:
        self._initialize = initialize
        self._sync_stage = sync_stage
        self._analyze_stage = analyze_stage
        self._finalize_daily_stage = finalize_daily_stage
        self._job_lock = threading.Lock()
        self._active_job_id: str | None = None

    async def execute(
        self,
        kind: PipelineKind,
        *,
        job_id: str | None = None,
    ) -> PipelineResult:
        result: PipelineResult = {}
        pipeline_date: date | None = None
        if kind in ("daily", "sync"):
            update_job_progress(
                job_id,
                progress=10,
                message="正在抓取并匹配 arXiv 候选",
            )
            result.update(await self._sync_stage())
            pipeline_date = date.fromisoformat(str(result["fetch_date"]))
        if kind in ("daily", "analyze"):
            update_job_progress(
                job_id,
                progress=35,
                message="正在进行 AI 摘要初筛与全文深读",
            )
            result.update(
                await self._analyze_stage(job_id=job_id, report_date=pipeline_date)
            )
        if kind == "daily":
            if pipeline_date is None:
                raise RuntimeError("Daily pipeline is missing its fetch date")
            result = await self._finalize_daily_stage(pipeline_date, result, job_id)
        return result

    async def run_daily(self, trigger: str = "manual") -> PipelineResult:
        """Run once in the caller process, used by the Docker scheduler."""

        self._initialize()
        with self._job_lock:
            if self._active_job_id is not None:
                return {
                    "accepted": False,
                    "success": False,
                    "message": "已有任务正在运行",
                }
            lock_token = f"direct:{uuid4()}"
            if not _acquire_pipeline_lock(lock_token):
                return {
                    "accepted": False,
                    "success": False,
                    "message": "已有任务正在运行",
                }
            try:
                with session_scope() as db:
                    existing = db.scalar(
                        select(Job)
                        .where(Job.status.in_(RUNNING_STATUSES))
                        .order_by(desc(Job.created_at))
                    )
                    if existing:
                        heartbeat = _as_utc(existing.started_at or existing.created_at)
                        if not heartbeat or heartbeat >= utcnow() - timedelta(hours=2):
                            _release_pipeline_lock(lock_token)
                            return {
                                "accepted": False,
                                "success": False,
                                "message": "已有任务正在运行",
                            }
                        existing.status = "failed"
                        existing.error = "应用退出后遗留的任务已自动关闭"
                        existing.completed_at = utcnow()
                    job = Job(
                        kind="daily",
                        status="running",
                        message=f"计划任务执行中（{trigger}）",
                        progress=5,
                        started_at=utcnow(),
                    )
                    db.add(job)
                    db.flush()
                    job_id = job.id
            except Exception:
                _release_pipeline_lock(lock_token)
                raise
            self._active_job_id = job_id
        try:
            result = await self.execute("daily", job_id=job_id)
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "completed"
                    job.progress = 100
                    job.result_json = result
                    job.message = "计划任务已完成"
                    job.completed_at = utcnow()
            return {"accepted": True, "success": True, **result}
        except Exception as exc:
            LOG.exception("Direct daily pipeline failed")
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)
                    job.message = "计划任务失败"
                    job.completed_at = utcnow()
            return {"accepted": True, "success": False, "error": str(exc)}
        finally:
            with self._job_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
            _release_pipeline_lock(lock_token)

    async def _run_job(self, job_id: str, kind: PipelineKind) -> None:
        try:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                job.status = "running"
                job.started_at = utcnow()
                job.message = "任务执行中"
                job.progress = 5
            result = await self.execute(kind, job_id=job_id)
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "completed"
                    job.progress = 100
                    job.result_json = result
                    job.message = "任务已完成"
                    job.completed_at = utcnow()
        except Exception as exc:
            LOG.exception("Background job %s failed", job_id)
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)
                    job.message = "任务失败"
                    job.completed_at = utcnow()
        finally:
            with self._job_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
            _release_pipeline_lock(job_id)

    def start_job(self, kind: PipelineKind) -> PipelineResult:
        self._initialize()
        with self._job_lock:
            if self._active_job_id is not None:
                raise HTTPException(status_code=409, detail="已有抓取或分析任务正在运行")
            job_id: str | None = None
            try:
                with session_scope() as db:
                    existing = db.scalar(
                        select(Job)
                        .where(Job.status.in_(RUNNING_STATUSES))
                        .order_by(desc(Job.created_at))
                    )
                    if existing:
                        heartbeat = _as_utc(existing.started_at or existing.created_at)
                        if heartbeat and heartbeat < utcnow() - timedelta(hours=2):
                            existing.status = "failed"
                            existing.error = "应用上次退出后遗留的任务已自动关闭"
                            existing.message = "任务超时"
                            existing.completed_at = utcnow()
                        else:
                            self._active_job_id = existing.id
                            raise HTTPException(
                                status_code=409,
                                detail="已有抓取或分析任务正在运行",
                            )
                    job_id = str(uuid4())
                    if not _acquire_pipeline_lock(job_id):
                        raise HTTPException(
                            status_code=409,
                            detail="已有抓取或分析任务正在运行",
                        )
                    db.add(
                        Job(
                            id=job_id,
                            kind=kind,
                            status="queued",
                            message="任务已排队",
                        )
                    )
            except Exception:
                if job_id is not None:
                    _release_pipeline_lock(job_id)
                raise
            self._active_job_id = job_id
        asyncio.create_task(self._run_job(job_id, kind))
        return {"job_id": job_id, "status": "queued", "message": "任务已在后台启动"}
