"""Canonicalize LLM-selected followed tags."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


def _value(topic: object | Mapping[str, Any], name: str, default: Any) -> Any:
    if isinstance(topic, Mapping):
        if name in topic:
            return topic[name]
        return topic.get(f"{name}_json", default)
    return getattr(topic, name, getattr(topic, f"{name}_json", default))


def terms(topic: object | Mapping[str, Any], name: str) -> list[str]:
    values = _value(topic, name, [])
    if not isinstance(values, list | tuple | set):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def direction_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def direction_names(topic: object | Mapping[str, Any]) -> tuple[str, ...]:
    canonical = str(_value(topic, "name", "")).strip()
    return tuple(value for value in (canonical, *terms(topic, "aliases")) if value)


def canonical_requested_direction_names(
    requested: object,
    topics: Sequence[object | Mapping[str, Any]],
    *,
    relevant: bool,
) -> list[str]:
    """Keep enabled configured tags selected by the LLM and normalize aliases."""

    if not relevant or not isinstance(requested, list | tuple | set):
        return []
    requested_keys = {
        key for value in requested if isinstance(value, str) and (key := direction_key(value))
    }
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not bool(_value(topic, "enabled", True)):
            continue
        name = str(_value(topic, "name", "")).strip()
        key = direction_key(name)
        accepted_keys = {direction_key(value) for value in direction_names(topic)}
        if name and accepted_keys & requested_keys and key not in seen:
            seen.add(key)
            result.append(name)
    return result


__all__ = ["canonical_requested_direction_names", "terms"]
