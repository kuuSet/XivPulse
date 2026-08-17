"""JSON-ready followed-interest views."""

from __future__ import annotations

from typing import Any

from ...models import Profile
from ...settings import RuntimeSettings


def _interests_wire(
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
                "aliases": topic.aliases,
                "query": topic.query,
                "arxiv_categories": topic.arxiv_categories,
                "keywords": topic.keywords,
                "context_keywords": topic.context_keywords,
                "negative_keywords": topic.negative_keywords,
                "weight": topic.weight,
                "enabled": topic.enabled,
            }
            for topic in profile.topics
        ],
    }
