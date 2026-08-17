"""Small SQLite schema upgrades for installations without Alembic."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text

LOG = logging.getLogger("xivpulse")


def upgrade_schema(engine: Engine) -> bool:
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("paper_matches"):
        return False

    columns = {column["name"] for column in inspect(engine).get_columns("paper_matches")}
    with engine.begin() as connection:
        if "relevance_score" in columns and "screening_relevance_score" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE paper_matches "
                    "RENAME COLUMN relevance_score TO screening_relevance_score"
                )
            )
            return True
    return False


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
