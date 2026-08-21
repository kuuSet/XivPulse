"""Canonicalize LLM-selected followed tags."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from ...models import Topic


def direction_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def direction_names(topic: Topic) -> tuple[str, ...]:
    return tuple(value for value in (topic.name, *topic.aliases) if value)


def canonical_requested_direction_names(
    requested: Sequence[str],
    topics: Sequence[Topic],
    *,
    relevant: bool,
) -> list[str]:
    """Keep enabled configured tags selected by the LLM and normalize aliases."""

    if not relevant:
        return []
    requested_keys = {key for value in requested if (key := direction_key(value))}
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not topic.enabled:
            continue
        name = topic.name.strip()
        key = direction_key(name)
        accepted_keys = {direction_key(value) for value in direction_names(topic)}
        if name and accepted_keys & requested_keys and key not in seen:
            seen.add(key)
            result.append(name)
    return result


__all__ = ["canonical_requested_direction_names"]
