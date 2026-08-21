"""JSON-ready followed-interest views."""

from __future__ import annotations

from typing import Any

from ...models import Profile
from ...settings import RuntimeSettings


def interests_view(
    profile: Profile,
    settings: RuntimeSettings,
) -> dict[str, Any]:
    return {
        "watched_authors": list(settings.watched_authors),
        "watched_organizations": list(settings.watched_organizations),
        "topics": [
            {
                "id": topic.id,
                "name": topic.name,
                "description": topic.description,
                "aliases": topic.aliases,
                "arxiv_categories": topic.arxiv_categories,
                "enabled": topic.enabled,
            }
            for topic in profile.topics
        ],
    }


__all__ = ["interests_view"]
