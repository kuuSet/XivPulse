"""Database upgrade tests."""

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


def test_upgrade_schema_removes_profile_metadata_without_losing_relations() -> None:
    engine = make_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE profiles ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "name VARCHAR(120) NOT NULL UNIQUE, "
                "description TEXT NOT NULL, "
                "language VARCHAR(20) NOT NULL, "
                "active BOOLEAN NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE topics ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "profile_id INTEGER NOT NULL REFERENCES profiles(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE paper_matches ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "profile_id INTEGER NOT NULL REFERENCES profiles(id), "
                "screening_relevance_score FLOAT, screened_at DATETIME, reason TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE analyses ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "profile_id INTEGER NOT NULL REFERENCES profiles(id), status VARCHAR(20))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE screening_queue ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "profile_id INTEGER NOT NULL REFERENCES profiles(id))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO profiles VALUES "
                "(7, 'Legacy profile', 'Must not affect decisions', 'zh-CN', 1, "
                "'2026-08-01 00:00:00', '2026-08-02 00:00:00')"
            )
        )
        connection.execute(text("INSERT INTO topics VALUES (11, 7)"))
        connection.execute(
            text(
                "INSERT INTO paper_matches VALUES "
                "(12, 7, 91, '2026-08-03 00:00:00', 'Legacy profile decision')"
            )
        )
        connection.execute(text("INSERT INTO analyses VALUES (13, 7, 'completed')"))
        connection.execute(text("INSERT INTO screening_queue VALUES (14, 7)"))

    assert upgrade_schema(engine) is True
    assert upgrade_schema(engine) is False

    assert [column["name"] for column in inspect(engine).get_columns("profiles")] == [
        "id",
        "created_at",
        "updated_at",
    ]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM profiles")).scalar_one() == 7
        assert connection.execute(text("SELECT profile_id FROM topics")).scalar_one() == 7
        match = connection.execute(
            text(
                "SELECT screening_relevance_score, screened_at, reason FROM paper_matches"
            )
        ).one()
        assert match == (None, None, "")
        assert connection.execute(text("SELECT status FROM analyses")).scalar_one() == "stale"
        assert connection.execute(text("SELECT count(*) FROM screening_queue")).scalar_one() == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_mock_cleanup_restores_semantic_scores_and_preserves_real_ai_scores() -> None:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        profile = session.query(Profile).one()
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
