"""Controlled method and evidence signals shared by reports."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..methods.catalog import match_method_labels
from .records import (
    _EVIDENCE_TERMS,
    _clean_phrase,
    _key,
    _PaperSignal,
    _payload,
    _payload_list,
)


def _has_term(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9+.-]+", term, re.I):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text, re.I) is not None
    return term.casefold() in text.casefold()


def _controlled_terms(text: str, vocabulary: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    return [label for label, terms in vocabulary if any(_has_term(text, term) for term in terms)]


def _method_labels(
    record: _PaperSignal,
    custom_terms: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return stable, controlled labels for cross-paper aggregation."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "methods")
    text = "\n".join(
        (
            record.paper.title,
            record.paper.abstract,
            " ".join(record.paper.categories),
            record.analysis.summary if record.analysis is not None else "",
            " ".join(explicit),
        )
    )
    return match_method_labels(text, custom_terms)


def _evidence_labels(record: _PaperSignal) -> list[str]:
    """Return evidence types, never paper-specific evidence statements."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "evidence")
    text = "\n".join(
        (
            record.paper.title,
            record.paper.abstract,
            record.analysis.summary if record.analysis is not None else "",
            " ".join(explicit),
        )
    )
    return _controlled_terms(text, _EVIDENCE_TERMS)


def _method_items(record: _PaperSignal) -> list[str]:
    """Return paper-level method details for the paper highlight."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "methods")
    return _unique_phrases([*explicit, *_method_labels(record)])


def _evidence_items(record: _PaperSignal) -> list[str]:
    """Return paper-level evidence details for the paper highlight."""

    payload = _payload(record.analysis)
    explicit = _payload_list(payload, "evidence")
    return _unique_phrases([*explicit, *_evidence_labels(record)])


def _unique_phrases(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _clean_phrase(raw)
        normalized = _key(text)
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return result
