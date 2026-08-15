"""Small additive SQLite migrations for existing local databases."""

from __future__ import annotations

import json

from sqlalchemy import Engine, inspect, text

from .database_defaults import (
    _CONTEXT_ANCHOR_MIGRATION_KEY,
    _CONTEXT_ANCHOR_MIGRATION_VERSION,
    _LEGACY_CONTEXT_KEYWORDS,
    DEFAULT_TOPICS,
)


def migrate_schema(bind: Engine) -> None:
    """Apply the small additive migrations needed by existing local databases."""

    if bind.dialect.name != "sqlite":
        return
    table_names = set(inspect(bind).get_table_names())
    if "topics" not in table_names:
        return
    columns = {column["name"] for column in inspect(bind).get_columns("topics")}
    aliases_column_added = "aliases_json" not in columns
    context_column_added = "context_keywords_json" not in columns
    with bind.begin() as connection:
        if "analyses" in table_names:
            analysis_columns = {column["name"] for column in inspect(bind).get_columns("analyses")}
            analysis_additions = {
                "budget_mode": "VARCHAR(20) NOT NULL DEFAULT 'balanced'",
                "usage_available": "BOOLEAN NOT NULL DEFAULT 0",
                "input_tokens": "INTEGER",
                "cached_input_tokens": "INTEGER",
                "output_tokens": "INTEGER",
                "reasoning_tokens": "INTEGER",
                "total_tokens": "INTEGER",
            }
            for name, definition in analysis_additions.items():
                if name not in analysis_columns:
                    connection.execute(text(f"ALTER TABLE analyses ADD COLUMN {name} {definition}"))
        if "paper_matches" in table_names:
            match_columns = {
                column["name"] for column in inspect(bind).get_columns("paper_matches")
            }
            match_additions = {
                "semantic_score": "FLOAT",
                "semantic_topics_json": "JSON NOT NULL DEFAULT '{}'",
                "semantic_model": "VARCHAR(160)",
            }
            for name, definition in match_additions.items():
                if name not in match_columns:
                    connection.execute(
                        text(f"ALTER TABLE paper_matches ADD COLUMN {name} {definition}")
                    )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_paper_matches_semantic_score "
                    "ON paper_matches (semantic_score)"
                )
            )
        if "papers" in table_names and "affiliations_json" not in {
            column["name"] for column in inspect(bind).get_columns("papers")
        }:
            connection.execute(
                text("ALTER TABLE papers ADD COLUMN affiliations_json JSON NOT NULL DEFAULT '[]'")
            )
        if "paper_versions" in table_names and "affiliations_json" not in {
            column["name"] for column in inspect(bind).get_columns("paper_versions")
        }:
            connection.execute(
                text(
                    "ALTER TABLE paper_versions ADD COLUMN affiliations_json JSON "
                    "NOT NULL DEFAULT '[]'"
                )
            )
        if "research_projects" in table_names and "kind" not in {
            column["name"] for column in inspect(bind).get_columns("research_projects")
        }:
            connection.execute(
                text(
                    "ALTER TABLE research_projects ADD COLUMN kind VARCHAR(30) "
                    "NOT NULL DEFAULT 'custom'"
                )
            )
        if "research_projects" in table_names:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_research_projects_kind "
                    "ON research_projects (kind)"
                )
            )
        if aliases_column_added:
            connection.execute(
                text("ALTER TABLE topics ADD COLUMN aliases_json JSON NOT NULL DEFAULT '[]'")
            )
            for topic in DEFAULT_TOPICS:
                aliases = topic.get("aliases_json", [])
                if not aliases:
                    continue
                connection.execute(
                    text(
                        "UPDATE topics SET aliases_json = :aliases "
                        "WHERE name = :name AND aliases_json = '[]'"
                    ),
                    {
                        "name": topic["name"],
                        "aliases": json.dumps(aliases, ensure_ascii=False),
                    },
                )
        if context_column_added:
            connection.execute(
                text(
                    "ALTER TABLE topics ADD COLUMN context_keywords_json JSON NOT NULL DEFAULT '[]'"
                )
            )
            for topic in DEFAULT_TOPICS:
                anchors = topic.get("context_keywords_json", [])
                if not anchors:
                    continue
                connection.execute(
                    text(
                        "UPDATE topics SET context_keywords_json = :anchors "
                        "WHERE name = :name AND context_keywords_json = '[]'"
                    ),
                    {
                        "name": topic["name"],
                        "anchors": json.dumps(anchors, ensure_ascii=False),
                    },
                )

        if "settings" not in table_names:
            return
        completed = connection.execute(
            text("SELECT value_json FROM settings WHERE key = :key"),
            {"key": _CONTEXT_ANCHOR_MIGRATION_KEY},
        ).scalar_one_or_none()
        if completed is not None:
            return

        # The first anchor defaults included generic words such as ``control``
        # and ``agent``.  Replace only byte-order-independent JSON arrays that
        # still equal those exact prototype defaults.  Any user-edited value,
        # including an explicit empty list, is preserved.
        rows = connection.execute(
            text(
                "SELECT id, name, context_keywords_json FROM topics "
                "WHERE name IN (:robot_learning, :world_model)"
            ),
            {
                "robot_learning": "机器人学习与强化学习",
                "world_model": "世界模型与规划",
            },
        ).mappings()
        defaults_by_name = {
            str(topic["name"]): list(topic.get("context_keywords_json", []))
            for topic in DEFAULT_TOPICS
        }
        for row in rows:
            raw_value = row["context_keywords_json"]
            try:
                current = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except (TypeError, json.JSONDecodeError):
                continue
            name = str(row["name"])
            if current != _LEGACY_CONTEXT_KEYWORDS.get(name):
                continue
            connection.execute(
                text("UPDATE topics SET context_keywords_json = :anchors WHERE id = :id"),
                {
                    "id": row["id"],
                    "anchors": json.dumps(defaults_by_name[name], ensure_ascii=False),
                },
            )

        connection.execute(
            text(
                "INSERT INTO settings (key, value_json, updated_at) "
                "VALUES (:key, :value, CURRENT_TIMESTAMP)"
            ),
            {
                "key": _CONTEXT_ANCHOR_MIGRATION_KEY,
                "value": json.dumps(_CONTEXT_ANCHOR_MIGRATION_VERSION),
            },
        )
