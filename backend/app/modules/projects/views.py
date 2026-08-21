"""JSON-ready reading and project views."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ...models import PaperReadingState, ProjectPaper, ResearchProject
from .library import FAVORITES_LIBRARY_KIND


def _reading_wire(reading: PaperReadingState | None) -> dict[str, Any]:
    return {
        "state": reading.state if reading else "unread",
        "priority": reading.priority if reading else 3,
        "notes": reading.notes if reading else "",
        "tags": reading.tags if reading else [],
        "created_at": reading.created_at if reading else None,
        "updated_at": reading.updated_at if reading else None,
    }


def _project_membership_wire(link: ProjectPaper, *, include_paper: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": link.id,
        "project_id": link.project_id,
        "paper_id": link.paper_id,
        "project_name": link.project.name if link.project else None,
        "project_color": link.project.color if link.project else None,
        "reading_state": link.reading_state,
        "priority": link.priority,
        "notes": link.notes,
        "tags": link.tags,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
    }
    if include_paper:
        result["paper"] = {
            "id": link.paper.id,
            "arxiv_id": link.paper.arxiv_id,
            "title": link.paper.title,
            "authors": link.paper.authors,
            "primary_category": link.paper.primary_category,
            "published_at": link.paper.published_at,
        }
    return result


def _project_wire(project: ResearchProject, *, detail: bool = False) -> dict[str, Any]:
    state_counts = Counter(link.reading_state for link in project.paper_links)
    result: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "kind": project.kind,
        "is_system": project.kind == FAVORITES_LIBRARY_KIND,
        "status": project.status,
        "color": project.color,
        "goal": project.goal,
        "due_date": project.due_date,
        "paper_count": len(project.paper_links),
        "reading_counts": dict(state_counts),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if detail:
        result["papers"] = [
            _project_membership_wire(link, include_paper=True) for link in project.paper_links
        ]
    return result
