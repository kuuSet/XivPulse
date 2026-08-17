"""Versioned fingerprints for research-direction attribution."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

DIRECTION_ATTRIBUTION_VERSION = "profile-topic-v4-aliases"
DIRECTION_ATTRIBUTION_FIELD = "_direction_attribution"


def _value(topic: object | Mapping[str, Any], name: str, default: Any) -> Any:
    if isinstance(topic, Mapping):
        if name in topic:
            return topic[name]
        return topic.get(f"{name}_json", default)
    return getattr(topic, name, getattr(topic, f"{name}_json", default))


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().split())


def _normalized_terms(values: object) -> list[str]:
    if not isinstance(values, list | tuple | set):
        return []
    return sorted({normalized.casefold() for value in values if (normalized := _normalized(value))})


def direction_taxonomy_fingerprint(
    topics: Sequence[object | Mapping[str, Any]],
) -> str:
    """Hash the enabled direction names and their positive/negative evidence terms."""

    entries: list[dict[str, Any]] = []
    for topic in topics:
        if not bool(_value(topic, "enabled", True)):
            continue
        name = _normalized(_value(topic, "name", ""))
        if not name:
            continue
        entries.append(
            {
                "name": name.casefold(),
                "aliases": _normalized_terms(_value(topic, "aliases", [])),
                "keywords": _normalized_terms(_value(topic, "keywords", [])),
                "context_keywords": _normalized_terms(_value(topic, "context_keywords", [])),
                "negative_keywords": _normalized_terms(_value(topic, "negative_keywords", [])),
            }
        )
    entries.sort(key=lambda item: item["name"])
    encoded = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stamp_direction_attribution(
    payload: Mapping[str, Any],
    topics: Sequence[object | Mapping[str, Any]],
) -> dict[str, Any]:
    stamped = dict(payload)
    stamped[DIRECTION_ATTRIBUTION_FIELD] = {
        "version": DIRECTION_ATTRIBUTION_VERSION,
        "taxonomy_fingerprint": direction_taxonomy_fingerprint(topics),
    }
    return stamped


def has_current_direction_attribution(
    payload: Mapping[str, Any],
    expected_taxonomy_fingerprint: str,
) -> bool:
    metadata = payload.get(DIRECTION_ATTRIBUTION_FIELD)
    return (
        isinstance(metadata, Mapping)
        and metadata.get("version") == DIRECTION_ATTRIBUTION_VERSION
        and metadata.get("taxonomy_fingerprint") == expected_taxonomy_fingerprint
    )


__all__ = [
    "DIRECTION_ATTRIBUTION_FIELD",
    "DIRECTION_ATTRIBUTION_VERSION",
    "direction_taxonomy_fingerprint",
    "has_current_direction_attribution",
    "stamp_direction_attribution",
]
