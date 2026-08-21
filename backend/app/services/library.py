"""Saved-paper and favorites-library state transitions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Feedback, PaperReadingState, ProjectPaper, ResearchProject

FAVORITES_LIBRARY_KIND = "favorites"
FAVORITES_LIBRARY_NAME = "收藏库"
FAVORITES_LIBRARY_COLOR = "#b54747"
FAVORITES_LIBRARY_DESCRIPTION = "收藏的论文会自动归入这里。"


def set_saved_feedback(
    db: Session,
    profile_id: int,
    paper_id: int,
    saved: bool,
) -> Feedback:
    feedback = db.scalar(
        select(Feedback).where(
            Feedback.paper_id == paper_id,
            Feedback.profile_id == profile_id,
        )
    )
    if feedback is None:
        feedback = Feedback(paper_id=paper_id, profile_id=profile_id)
        db.add(feedback)
    if saved:
        feedback.action = "save"
    elif feedback.action == "save":
        feedback.action = "neutral"
    return feedback


def sync_favorites_membership(
    db: Session,
    profile_id: int,
    paper_id: int,
    *,
    saved: bool,
) -> ProjectPaper | None:
    library = db.scalars(
        select(ResearchProject).where(
            ResearchProject.profile_id == profile_id,
            ResearchProject.kind == FAVORITES_LIBRARY_KIND,
        )
    ).one()
    membership = db.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == library.id,
            ProjectPaper.paper_id == paper_id,
        )
    )
    if saved and membership is None:
        reading = db.scalar(
            select(PaperReadingState).where(
                PaperReadingState.paper_id == paper_id,
                PaperReadingState.profile_id == profile_id,
            )
        )
        membership = ProjectPaper(
            project_id=library.id,
            paper_id=paper_id,
            reading_state=(
                reading.state if reading is not None and reading.state != "unread" else "queued"
            ),
            priority=reading.priority if reading is not None else 3,
            tags_json=reading.tags if reading is not None else [],
        )
        db.add(membership)
    elif not saved and membership is not None:
        db.delete(membership)
        membership = None
    return membership
