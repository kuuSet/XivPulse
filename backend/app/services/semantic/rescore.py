"""Historical semantic score recalculation with fingerprint-based skipping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ...database import SessionLocal, session_scope
from ...models import Paper, PaperMatch
from ...settings import set_setting, setting_value
from ..state import active_profile
from .matcher import SemanticMatcher

FINGERPRINT_KEY = "semantic_score_fingerprint"
STATUS_KEY = "semantic_rescore_status"
ProgressCallback = Callable[[int, int], None]


class SemanticRescorer:
    """Recalculate stored scores without reopening screening or delivery work."""

    def __init__(
        self,
        matcher: SemanticMatcher,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        batch_size: int = 500,
    ) -> None:
        self.matcher = matcher
        self.session_factory = session_factory
        self.batch_size = batch_size

    def status(self, db: Session) -> dict[str, Any]:
        target = self.matcher.fingerprint(active_profile(db).topics)
        stored_fingerprint = setting_value(db, FINGERPRINT_KEY)
        stored_status = self._stored_status(db)
        if (
            stored_status.get("status") in {"running", "failed"}
            and stored_status.get("fingerprint") == target
        ):
            return self._public_status(stored_status)
        if stored_fingerprint != target or stored_status.get("fingerprint") != target:
            return {"status": "pending"}
        return self._public_status(stored_status)

    def rescore(
        self,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        target_fingerprint = ""
        processed = 0
        total = 0
        try:
            with session_scope(self.session_factory) as db:
                profile = active_profile(db)
                profile_id = profile.id
                topics = list(profile.topics)
                target_fingerprint = self.matcher.fingerprint(topics)
                stored_fingerprint = setting_value(db, FINGERPRINT_KEY)
                stored_status = self._stored_status(db)
                if (
                    not force
                    and stored_fingerprint == target_fingerprint
                    and stored_status.get("status") == "completed"
                ):
                    result = self._public_status(stored_status)
                    return {**result, "forced": False, "skipped": True}

                total = int(
                    db.scalar(
                        select(func.count())
                        .select_from(PaperMatch)
                        .where(PaperMatch.profile_id == profile_id)
                    )
                    or 0
                )
                self._set_status(
                    db,
                    status="running",
                    fingerprint=target_fingerprint,
                    processed=0,
                    total=total,
                )

            last_paper_id = 0
            while processed < total:
                with session_scope(self.session_factory) as db:
                    rows = db.execute(
                        select(Paper, PaperMatch)
                        .join(PaperMatch, PaperMatch.paper_id == Paper.id)
                        .where(
                            PaperMatch.profile_id == profile_id,
                            Paper.id > last_paper_id,
                        )
                        .order_by(Paper.id)
                        .limit(self.batch_size)
                    ).all()
                    if not rows:
                        break
                    papers = [row.Paper for row in rows]
                    results = self.matcher.match(db, papers, topics)
                    for row in rows:
                        match = results.get(row.Paper.id)
                        row.PaperMatch.semantic_score = match.score if match else None
                        row.PaperMatch.semantic_topics_json = match.topic_scores if match else {}
                        row.PaperMatch.semantic_model = self.matcher.model_name if match else None
                    processed += len(rows)
                    last_paper_id = papers[-1].id
                    self._set_status(
                        db,
                        status="running",
                        fingerprint=target_fingerprint,
                        processed=processed,
                        total=total,
                    )
                if progress:
                    progress(processed, total)

            with session_scope(self.session_factory) as db:
                current_fingerprint = self.matcher.fingerprint(active_profile(db).topics)
                if current_fingerprint != target_fingerprint:
                    self._set_status(db, status="pending")
                    return {
                        "status": "pending",
                        "processed": processed,
                        "total": total,
                        "forced": force,
                        "skipped": False,
                    }
                set_setting(db, FINGERPRINT_KEY, target_fingerprint)
                self._set_status(
                    db,
                    status="completed",
                    fingerprint=target_fingerprint,
                    processed=processed,
                    total=total,
                )
            return {
                "status": "completed",
                "processed": processed,
                "total": total,
                "forced": force,
                "skipped": False,
            }
        except Exception as exc:
            with session_scope(self.session_factory) as db:
                current_fingerprint = self.matcher.fingerprint(active_profile(db).topics)
                if target_fingerprint and current_fingerprint != target_fingerprint:
                    self._set_status(db, status="pending")
                else:
                    self._set_status(
                        db,
                        status="failed",
                        fingerprint=target_fingerprint,
                        processed=processed,
                        total=total,
                        error=str(exc)[:1000],
                    )
            raise

    @staticmethod
    def _stored_status(db: Session) -> dict[str, Any]:
        value = setting_value(db, STATUS_KEY, {"status": "pending"})
        return dict(value) if isinstance(value, dict) else {"status": "pending"}

    @staticmethod
    def _public_status(status: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in status.items() if key != "fingerprint"}

    @staticmethod
    def _set_status(db: Session, *, status: str, **values: Any) -> None:
        set_setting(db, STATUS_KEY, {"status": status, **values})


__all__ = ["SemanticRescorer"]
