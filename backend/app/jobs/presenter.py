"""JSON-ready background job views."""

from __future__ import annotations

from typing import Any

from ..models import Job


def _job_wire(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    data = {
        "id": job.id,
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "result": job.result,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
    for key in (
        "prefiltered",
        "prefilter_kept",
        "prefilter_rejected",
        "screening_total",
        "screened",
        "analyzed",
    ):
        if key in job.result:
            data[key] = job.result[key]
    return data
