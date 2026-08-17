"""Auditable research-direction matching rules."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


def contains_term(text: str, phrase: object) -> bool:
    value = str(phrase).strip()
    if not value:
        return False
    if re.fullmatch(r"[A-Za-z0-9_+ .-]+", value):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.I) is not None
    return value.casefold() in text.casefold()


def direction_key(value: object) -> str:
    """Normalize exact direction spellings, including harmless separators."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s_-]+", "", normalized).casefold()


def direction_names(topic: object | Mapping[str, Any]) -> tuple[str, ...]:
    """Return the canonical name followed by configured aliases."""

    canonical = str(_value(topic, "name", "")).strip()
    return tuple(value for value in (canonical, *terms(topic, "aliases")) if value)


@dataclass(frozen=True, slots=True)
class DirectionEvidence:
    matched: bool
    keywords: tuple[str, ...]
    context_anchors: tuple[str, ...]
    exclusions: tuple[str, ...]


def direction_evidence(
    text: str,
    topic: object | Mapping[str, Any],
) -> DirectionEvidence:
    """Require a direction term and, when configured, a domain-context anchor."""

    keyword_hits = tuple(value for value in terms(topic, "keywords") if contains_term(text, value))
    anchor_terms = terms(topic, "context_keywords")
    anchor_hits = tuple(value for value in anchor_terms if contains_term(text, value))
    exclusion_hits = tuple(
        value for value in terms(topic, "negative_keywords") if contains_term(text, value)
    )
    return DirectionEvidence(
        matched=bool(keyword_hits)
        and not exclusion_hits
        and (not anchor_terms or bool(anchor_hits)),
        keywords=keyword_hits,
        context_anchors=anchor_hits,
        exclusions=exclusion_hits,
    )


def matched_direction_names(
    text: str,
    topics: Sequence[object | Mapping[str, Any]],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not bool(_value(topic, "enabled", True)):
            continue
        evidence = direction_evidence(text, topic)
        name = str(_value(topic, "name", "")).strip()
        key = name.casefold()
        if evidence.matched and name and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def verified_requested_direction_names(
    text: str,
    requested: object,
    topics: Sequence[object | Mapping[str, Any]],
    *,
    relevant: bool,
) -> list[str]:
    """Accept model-selected directions only when current local evidence agrees.

    The model may select only an exact configured direction name.  It cannot
    bypass direction keywords, context anchors, exclusions, or a negative
    relevance verdict.
    """

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
        if (
            name
            and bool(accepted_keys & requested_keys)
            and key not in seen
            and direction_evidence(text, topic).matched
        ):
            seen.add(key)
            result.append(name)
    return result


def canonical_requested_direction_names(
    requested: object,
    topics: Sequence[object | Mapping[str, Any]],
    *,
    relevant: bool,
) -> list[str]:
    """Canonicalize model-selected controlled directions without a lexical veto."""

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


__all__ = [
    "DirectionEvidence",
    "canonical_requested_direction_names",
    "contains_term",
    "direction_key",
    "direction_names",
    "direction_evidence",
    "matched_direction_names",
    "terms",
    "verified_requested_direction_names",
]
