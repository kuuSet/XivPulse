"""Database upgrade tests for the score-source migration."""

from datetime import UTC, date, datetime

import pytest
from app.database import init_db, make_engine
from app.models import Analysis, Paper, PaperMatch, Profile, ScreeningQueueItem
from app.schema_migrations import remove_mock_analyses, upgrade_schema
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


def test_upgrade_schema_renames_ambiguous_match_score() -> None:
    engine = make_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE paper_matches (id INTEGER PRIMARY KEY, relevance_score FLOAT)")
        )

    assert upgrade_schema(engine) is True
    assert upgrade_schema(engine) is False

    columns = {column["name"] for column in inspect(engine).get_columns("paper_matches")}
    assert "screening_relevance_score" in columns
    assert "relevance_score" not in columns


def test_mock_cleanup_restores_semantic_scores_and_preserves_real_ai_scores() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        profile = session.query(Profile).filter(Profile.active.is_(True)).one()
        mock_paper = Paper(arxiv_id="2608.00001", title="Mocked", abstract="Abstract")
        ai_paper = Paper(arxiv_id="2608.00002", title="Real AI", abstract="Abstract")
        session.add_all((mock_paper, ai_paper))
        session.flush()
        mock_match = PaperMatch(
            paper_id=mock_paper.id,
            profile_id=profile.id,
            semantic_score=0.8,
            screening_relevance_score=12,
            screened_at=now,
        )
        ai_match = PaperMatch(
            paper_id=ai_paper.id,
            profile_id=profile.id,
            semantic_score=0.7,
            screening_relevance_score=12,
            screened_at=now,
        )
        session.add_all((mock_match, ai_match))
        session.flush()
        session.add_all(
            (
                Analysis(
                    paper_id=mock_paper.id,
                    profile_id=profile.id,
                    kind="screening",
                    provider="openai",
                    model="gpt-test",
                    transport="mock",
                    relevance_score=12,
                ),
                Analysis(
                    paper_id=ai_paper.id,
                    profile_id=profile.id,
                    kind="screening",
                    provider="test-provider",
                    model="test-model",
                    transport="responses",
                    relevance_score=91,
                    created_at=now,
                ),
                ScreeningQueueItem(
                    paper_id=mock_paper.id,
                    profile_id=profile.id,
                    report_date=date.today(),
                    status="completed",
                ),
                ScreeningQueueItem(
                    paper_id=ai_paper.id,
                    profile_id=profile.id,
                    report_date=date.today(),
                    status="completed",
                ),
            )
        )
        mock_match_id = mock_match.id
        ai_match_id = ai_match.id
        session.commit()

    remove_mock_analyses(engine)

    with Session(engine) as session:
        mock_match = session.get(PaperMatch, mock_match_id)
        ai_match = session.get(PaperMatch, ai_match_id)
        assert mock_match is not None and ai_match is not None
        assert mock_match.screening_relevance_score is None
        assert mock_match.screened_at is None
        assert mock_match.effective_score == pytest.approx(80)
        assert ai_match.screening_relevance_score == pytest.approx(91)
        assert ai_match.score_source == "ai"
        assert session.query(Analysis).filter_by(transport="mock").count() == 0
        assert session.query(ScreeningQueueItem).count() == 1
