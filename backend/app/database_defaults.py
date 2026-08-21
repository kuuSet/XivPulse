"""Built-in followed-topic seed data."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .services.directions.catalog import DIRECTION_CATALOG

_DEFAULT_DESCRIPTIONS = {
    "embodied-generalist": (
        "Embodied AI systems that integrate perception, reasoning, learning, planning, "
        "and physical action in real-world robots."
    ),
    "vision-language-action": (
        "Vision-language-action models that map visual and language inputs to robot "
        "actions and policies."
    ),
    "foundation-language-models": (
        "Large language model systems covering pretraining, adaptation, reasoning, "
        "long-context processing, and scalable deployment."
    ),
    "ai-agents": (
        "LLM agents that use tools, memory, planning, and multi-agent coordination to "
        "complete long-horizon tasks."
    ),
    "ml-systems": (
        "AI infrastructure for distributed training, compilers, parallelism, resource "
        "scheduling, and reliable model serving."
    ),
    "efficient-model-inference": (
        "Efficient LLM inference and serving, including batching, caching, scheduling, "
        "decoding acceleration, throughput, and tail latency."
    ),
}


def _default_topic(slug: str) -> dict[str, object]:
    item = next(value for value in DIRECTION_CATALOG if value["slug"] == slug)
    return {
        "name": item["name"],
        "description": _DEFAULT_DESCRIPTIONS[slug],
        "aliases_json": list(item["aliases"]),
        "arxiv_categories_json": list(item["arxiv_categories"]),
        "keywords_json": list(item["keywords"]),
    }


DEFAULT_TOPICS = tuple(_default_topic(slug) for slug in _DEFAULT_DESCRIPTIONS)


def seed_defaults(session: Session) -> None:
    from .models import Profile, ResearchProject, Topic
    from .services.library import (
        FAVORITES_LIBRARY_COLOR,
        FAVORITES_LIBRARY_DESCRIPTION,
        FAVORITES_LIBRARY_KIND,
        FAVORITES_LIBRARY_NAME,
    )

    profile = session.scalar(select(Profile).order_by(Profile.id))
    if profile is None:
        profile = Profile()
        session.add(profile)
        session.flush()
        for values in DEFAULT_TOPICS:
            session.add(Topic(profile_id=profile.id, enabled=True, **values))
        session.add(
            ResearchProject(
                profile_id=profile.id,
                name=FAVORITES_LIBRARY_NAME,
                description=FAVORITES_LIBRARY_DESCRIPTION,
                kind=FAVORITES_LIBRARY_KIND,
                status="active",
                color=FAVORITES_LIBRARY_COLOR,
            )
        )
