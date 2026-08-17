"""Small SQLite schema upgrades for installations without Alembic."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

LOG = logging.getLogger("xivpulse")


def upgrade_schema(engine: Engine) -> bool:
    if engine.dialect.name != "sqlite":
        return False

    upgraded = _remove_profile_metadata(engine)
    inspector = inspect(engine)
    if inspector.has_table("paper_matches"):
        columns = {column["name"] for column in inspector.get_columns("paper_matches")}
        if "relevance_score" in columns and "screening_relevance_score" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE paper_matches "
                        "RENAME COLUMN relevance_score TO screening_relevance_score"
                    )
                )
            upgraded = True
    return upgraded


def _remove_profile_metadata(engine: Engine) -> bool:
    inspector = inspect(engine)
    if not inspector.has_table("profiles"):
        return False
    columns = {column["name"] for column in inspector.get_columns("profiles")}
    if not columns.intersection({"name", "description", "language", "active"}):
        return False

    connection = engine.raw_connection()
    cursor = connection.cursor()
    foreign_keys = True
    try:
        foreign_keys = bool(cursor.execute("PRAGMA foreign_keys").fetchone()[0])
        connection.commit()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("BEGIN")
        cursor.execute(
            "CREATE TABLE profiles_new ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
        cursor.execute(
            "INSERT INTO profiles_new (id, created_at, updated_at) "
            "SELECT id, created_at, updated_at FROM profiles"
        )
        cursor.execute("DROP TABLE profiles")
        cursor.execute("ALTER TABLE profiles_new RENAME TO profiles")
        tables = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "analyses" in tables:
            cursor.execute("UPDATE analyses SET status='stale' WHERE status='completed'")
        if "paper_matches" in tables:
            cursor.execute(
                "UPDATE paper_matches SET "
                "screening_relevance_score=NULL, screened_at=NULL, reason=''"
            )
        if "screening_queue" in tables:
            cursor.execute("DELETE FROM screening_queue")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        cursor.close()
        connection.close()
    return True


def remove_mock_analyses(engine: Engine) -> None:
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("analyses"):
        return

    with engine.begin() as connection:
        deleted = connection.execute(
            text(
                "DELETE FROM analyses "
                "WHERE provider = 'mock' OR model = 'mock' OR transport = 'mock'"
            )
        ).rowcount
        connection.execute(
            text(
                """
                UPDATE paper_matches
                SET screening_relevance_score = (
                        SELECT analyses.relevance_score
                        FROM analyses
                        WHERE analyses.paper_id = paper_matches.paper_id
                          AND analyses.profile_id = paper_matches.profile_id
                          AND analyses.kind = 'screening'
                          AND analyses.status = 'completed'
                        ORDER BY analyses.created_at DESC, analyses.id DESC
                        LIMIT 1
                    ),
                    screened_at = (
                        SELECT analyses.created_at
                        FROM analyses
                        WHERE analyses.paper_id = paper_matches.paper_id
                          AND analyses.profile_id = paper_matches.profile_id
                          AND analyses.kind = 'screening'
                          AND analyses.status = 'completed'
                        ORDER BY analyses.created_at DESC, analyses.id DESC
                        LIMIT 1
                    )
                """
            )
        )
        removed_queue = connection.execute(
            text(
                """
                DELETE FROM screening_queue
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM paper_matches
                    WHERE paper_matches.paper_id = screening_queue.paper_id
                      AND paper_matches.profile_id = screening_queue.profile_id
                      AND paper_matches.screened_at IS NOT NULL
                )
                """
            )
        ).rowcount
        if deleted:
            connection.execute(text("DELETE FROM report_synthesis_runs"))
            connection.execute(text("DELETE FROM daily_research_reports"))
            connection.execute(text("DELETE FROM digests"))
    if deleted or removed_queue:
        LOG.info(
            "Removed %d mock analyses and %d stale screening queue items",
            deleted,
            removed_queue,
        )


__all__ = ["remove_mock_analyses", "upgrade_schema"]
