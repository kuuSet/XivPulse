"""Method catalog and candidate HTTP routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Analysis, Paper, utcnow
from ..services.methods.catalog import (
    METHOD_CANDIDATE_MIN_PAPERS,
    METHOD_CANDIDATE_WINDOW_DAYS,
    clean_custom_methods,
    discover_method_candidates,
    method_catalog_payload,
    normalize_method_key,
)
from ..services.state import clean_string_list
from ..settings import load_runtime_settings, set_setting

router = APIRouter()


class MethodCandidateDecisionPayload(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=20)


class CustomMethodsPayload(BaseModel):
    items: list[CustomMethodPayload] = Field(default_factory=list, max_length=100)


def _method_candidate_records(db: Session, window_days: int) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=max(1, min(window_days, 365)))
    rows = db.execute(
        select(Analysis, Paper)
        .join(Paper, Paper.id == Analysis.paper_id)
        .where(
            Analysis.status == "completed",
            Analysis.created_at >= cutoff,
        )
        .order_by(desc(Analysis.created_at))
    ).all()
    # Prefer the newest method-bearing analysis for a paper. Screening usually
    # has no methods, while deep analysis provides the structured source.
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for analysis, paper in rows:
        methods = analysis.payload.get("methods", [])
        if paper.id in seen or not isinstance(methods, list) or not methods:
            continue
        seen.add(paper.id)
        result.append(
            {
                "paper_id": paper.id,
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "methods": methods,
            }
        )
    return result


@router.get("/api/methods/catalog")
def methods_catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    return method_catalog_payload(clean_custom_methods(list(settings.custom_methods)))


@router.get("/api/methods/candidates")
def method_candidates(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    window_days = METHOD_CANDIDATE_WINDOW_DAYS
    min_papers = METHOD_CANDIDATE_MIN_PAPERS
    custom = clean_custom_methods(list(settings.custom_methods))
    dismissed = list(settings.dismissed_method_candidates)
    candidates = discover_method_candidates(
        _method_candidate_records(db, window_days),
        custom_values=custom,
        dismissed=dismissed,
        min_papers=min_papers,
    )
    return {
        "window_days": window_days,
        "min_papers": max(2, min_papers),
        "count": len(candidates),
        "items": candidates,
        "automatic_accept": False,
        "token_cost": 0,
    }


@router.post("/api/methods/candidates/accept")
def accept_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    candidates = method_candidates(db)["items"]
    candidate = next(
        (item for item in candidates if item["key"] == normalize_method_key(payload.key)),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="该候选已不存在或不再满足重复门槛")
    name = str(payload.name or candidate["name"]).strip()
    custom = clean_custom_methods(list(settings.custom_methods))
    if normalize_method_key(name) in {normalize_method_key(item["name"]) for item in custom}:
        raise HTTPException(status_code=409, detail="自定义方法中已经存在同名标签")
    aliases = clean_string_list(
        [candidate["name"], *payload.aliases] if name != candidate["name"] else payload.aliases,
        limit=20,
    )
    custom.append({"name": name, "aliases": aliases})
    set_setting(db, "custom_methods", custom)
    db.commit()
    return method_catalog_payload(custom)


@router.post("/api/methods/candidates/dismiss")
def dismiss_method_candidate(
    payload: MethodCandidateDecisionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = load_runtime_settings(db)
    dismissed = list(settings.dismissed_method_candidates)
    key = normalize_method_key(payload.key)
    if key and key not in {normalize_method_key(item) for item in dismissed}:
        dismissed.append(key)
    set_setting(db, "dismissed_method_candidates", dismissed)
    db.commit()
    return {"ok": True, "dismissed_key": key}


@router.put("/api/methods/custom")
def replace_custom_methods(
    payload: CustomMethodsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    custom = clean_custom_methods([item.model_dump() for item in payload.items])
    set_setting(db, "custom_methods", custom)
    db.commit()
    return method_catalog_payload(custom)
