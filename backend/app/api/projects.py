"""Research-project and project-paper HTTP routes."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..models import Paper, ProjectPaper, ResearchProject
from ..profile import active_profile
from ..services.library import (
    FAVORITES_LIBRARY_KIND,
    set_saved_feedback,
)
from .presenters.common import _clean_tags
from .presenters.projects import _project_membership_wire, _project_wire

router = APIRouter()

ReadingStateValue = Literal["unread", "queued", "reading", "read", "reproduce", "cited", "archived"]
ProjectStatusValue = Literal["active", "paused", "completed"]


class ProjectCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20000)
    status: ProjectStatusValue = "active"
    color: str = Field(default="#2563eb", pattern=r"^#[0-9A-Fa-f]{6}$")
    goal: str = Field(default="", max_length=20000)
    due_date: date | None = None


class ProjectUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20000)
    status: ProjectStatusValue | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    goal: str | None = Field(default=None, max_length=20000)
    due_date: date | None = None


class ProjectPaperUpdatePayload(BaseModel):
    reading_state: ReadingStateValue | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=50)


def _project_or_404(db: Session, project_id: int, profile_id: int) -> ResearchProject:
    project = db.scalar(
        select(ResearchProject)
        .options(selectinload(ResearchProject.paper_links).selectinload(ProjectPaper.paper))
        .where(
            ResearchProject.id == project_id,
            ResearchProject.profile_id == profile_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="分类库不存在")
    return project


@router.get("/api/projects")
def list_projects(
    project_status: ProjectStatusValue | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    statement = (
        select(ResearchProject)
        .options(selectinload(ResearchProject.paper_links))
        .where(ResearchProject.profile_id == profile.id)
        .order_by(
            desc(ResearchProject.kind == FAVORITES_LIBRARY_KIND),
            desc(ResearchProject.updated_at),
            ResearchProject.id,
        )
    )
    if project_status is not None:
        statement = statement.where(ResearchProject.status == project_status)
    projects = db.scalars(statement).unique().all()
    return {"items": [_project_wire(project) for project in projects], "count": len(projects)}


@router.post("/api/projects", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreatePayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = active_profile(db)
    name = payload.name.strip()
    duplicate = db.scalar(
        select(ResearchProject.id).where(
            ResearchProject.profile_id == profile.id,
            func.lower(ResearchProject.name) == name.casefold(),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同名分类库已存在")
    project = ResearchProject(
        profile_id=profile.id,
        name=name,
        description=payload.description.strip(),
        status=payload.status,
        color=payload.color.lower(),
        goal=payload.goal.strip(),
        due_date=payload.due_date,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_wire(project, detail=True)


@router.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    profile = active_profile(db)
    return _project_wire(_project_or_404(db, project_id, profile.id), detail=True)


@router.patch("/api/projects/{project_id}")
def update_project(
    project_id: int, payload: ProjectUpdatePayload, db: Session = Depends(get_db)
) -> dict[str, Any]:
    profile = active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    if project.kind == FAVORITES_LIBRARY_KIND:
        raise HTTPException(status_code=409, detail="收藏库是系统分类，不能编辑")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        name = changes["name"].strip()
        duplicate = db.scalar(
            select(ResearchProject.id).where(
                ResearchProject.profile_id == profile.id,
                ResearchProject.id != project.id,
                func.lower(ResearchProject.name) == name.casefold(),
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="同名分类库已存在")
        project.name = name
    for field in ("description", "status", "color", "goal", "due_date"):
        if field not in changes:
            continue
        value = changes[field]
        if value is None and field != "due_date":
            continue
        if field in ("description", "goal"):
            value = value.strip()
        if field == "color":
            value = value.lower()
        setattr(project, field, value)
    db.commit()
    return _project_wire(_project_or_404(db, project_id, profile.id), detail=True)


@router.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> Response:
    profile = active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    if project.kind == FAVORITES_LIBRARY_KIND:
        raise HTTPException(status_code=409, detail="收藏库是系统分类，不能删除")
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _save_project_paper_by_id(
    db: Session,
    project: ResearchProject,
    paper: Paper,
    payload: ProjectPaperUpdatePayload | None,
    *,
    require_existing: bool,
) -> tuple[ProjectPaper, bool]:
    link = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id,
            ProjectPaper.paper_id == paper.id,
        )
    )
    created = link is None
    if link is None:
        if require_existing:
            raise HTTPException(status_code=404, detail="论文不在该分类库中")
        link = ProjectPaper(project_id=project.id, paper_id=paper.id)
        db.add(link)
    if payload is not None:
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("reading_state") is not None:
            link.reading_state = changes["reading_state"]
        if changes.get("priority") is not None:
            link.priority = changes["priority"]
        if changes.get("notes") is not None:
            link.notes = changes["notes"].strip()
        if changes.get("tags") is not None:
            link.tags = _clean_tags(changes["tags"])
    if project.kind == FAVORITES_LIBRARY_KIND:
        set_saved_feedback(db, project.profile_id, paper.id, True)
    db.commit()
    saved = db.scalar(
        select(ProjectPaper)
        .options(joinedload(ProjectPaper.project), joinedload(ProjectPaper.paper))
        .where(ProjectPaper.id == link.id)
    )
    assert saved is not None
    return saved, created


@router.post("/api/projects/{project_id}/papers/{paper_id}")
def add_project_paper_by_id(
    project_id: int,
    paper_id: int,
    payload: ProjectPaperUpdatePayload | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    link, created = _save_project_paper_by_id(db, project, paper, payload, require_existing=False)
    return {"ok": True, "created": created, **_project_membership_wire(link, include_paper=True)}


@router.patch("/api/projects/{project_id}/papers/{paper_id}")
def update_project_paper_by_id(
    project_id: int,
    paper_id: int,
    payload: ProjectPaperUpdatePayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    profile = active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")
    link, _ = _save_project_paper_by_id(db, project, paper, payload, require_existing=True)
    return {"ok": True, "created": False, **_project_membership_wire(link, include_paper=True)}


@router.delete(
    "/api/projects/{project_id}/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_project_paper(project_id: int, paper_id: int, db: Session = Depends(get_db)) -> Response:
    profile = active_profile(db)
    project = _project_or_404(db, project_id, profile.id)
    link = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id,
            ProjectPaper.paper_id == paper_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="论文不在该分类库中")
    if project.kind == FAVORITES_LIBRARY_KIND:
        set_saved_feedback(db, profile.id, paper_id, False)
    db.delete(link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
