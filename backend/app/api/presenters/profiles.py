"""JSON-ready research profile views."""

from __future__ import annotations

from typing import Any

from ...models import Profile
from ...settings import RuntimeSettings


def _profile_wire(
    profile: Profile,
    settings: RuntimeSettings,
) -> dict[str, Any]:
    enabled = [topic for topic in profile.topics if topic.enabled]
    positive = list(dict.fromkeys(keyword for topic in enabled for keyword in topic.keywords))
    negative = list(
        dict.fromkeys(keyword for topic in enabled for keyword in topic.negative_keywords)
    )
    categories = list(
        dict.fromkeys(category for topic in enabled for category in topic.arxiv_categories)
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "language": profile.language,
        "active": profile.active,
        "positive_keywords": positive,
        "negative_keywords": negative,
        "categories": categories,
        "watched_authors": list(settings.watched_authors),
        "watched_organizations": list(settings.watched_organizations),
        "prioritize_watched_authors": settings.prioritize_watched_authors,
        "prioritize_watched_organizations": settings.prioritize_watched_organizations,
        "min_relevance": settings.deep_analysis_threshold,
        "topics": [
            {
                "id": topic.id,
                "profile_id": topic.profile_id,
                "name": topic.name,
                "aliases": topic.aliases,
                "query": topic.query,
                "arxiv_categories": topic.arxiv_categories,
                "keywords": topic.keywords,
                "context_keywords": topic.context_keywords,
                "negative_keywords": topic.negative_keywords,
                "weight": topic.weight,
                "enabled": topic.enabled,
                "created_at": topic.created_at,
                "updated_at": topic.updated_at,
            }
            for topic in profile.topics
        ],
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
