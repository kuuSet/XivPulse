"""Saved-paper and favorites-library state transitions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Feedback, PaperReadingState, ProjectPaper, ResearchProject

FAVORITES_LIBRARY_KIND = "favorites"
FAVORITES_LIBRARY_NAME = "收藏库"
FAVORITES_LIBRARY_COLOR = "#b54747"


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


def ensure_favorites_library(db: Session, profile_id: int) -> ResearchProject:
    library = db.scalar(
        select(ResearchProject).where(
            ResearchProject.profile_id == profile_id,
            ResearchProject.kind == FAVORITES_LIBRARY_KIND,
        )
    )
    if library is not None:
        return library
    library = db.scalar(
        select(ResearchProject).where(
            ResearchProject.profile_id == profile_id,
            ResearchProject.name == FAVORITES_LIBRARY_NAME,
        )
    )
    if library is None:
        library = ResearchProject(
            profile_id=profile_id,
            name=FAVORITES_LIBRARY_NAME,
            description="收藏的论文会自动归入这里。",
            kind=FAVORITES_LIBRARY_KIND,
            status="active",
            color=FAVORITES_LIBRARY_COLOR,
        )
        db.add(library)
    else:
        library.kind = FAVORITES_LIBRARY_KIND
    db.flush()
    existing_paper_ids = {link.paper_id for link in library.paper_links}
    saved_paper_ids = set(
        db.scalars(
            select(Feedback.paper_id).where(
                Feedback.profile_id == profile_id,
                Feedback.action == "save",
            )
        ).all()
    )
    for paper_id in saved_paper_ids - existing_paper_ids:
        db.add(
            ProjectPaper(
                project_id=library.id,
                paper_id=paper_id,
                reading_state="queued",
            )
        )
    for paper_id in existing_paper_ids - saved_paper_ids:
        set_saved_feedback(db, profile_id, paper_id, True)
    return library


def sync_favorites_membership(
    db: Session,
    profile_id: int,
    paper_id: int,
    *,
    saved: bool,
) -> ProjectPaper | None:
    library = ensure_favorites_library(db, profile_id)
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
