"""Normalization for external text and arXiv identifiers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_ARXIV_ID_RE = re.compile(
    r"(?P<id>(?:[a-z][a-z.\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NormalizedArxivId:
    base_id: str
    version: int = 1


def parse_arxiv_id(value: str) -> NormalizedArxivId:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("arXiv identifier cannot be empty")
    cleaned = html.unescape(value).strip()
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
    cleaned = re.sub(r"\.pdf$", "", cleaned, flags=re.IGNORECASE).rstrip("/")
    match = _ARXIV_ID_RE.search(cleaned)
    if match is None:
        raise ValueError(f"Invalid arXiv identifier: {value!r}")
    base_id = match.group("id")
    # Legacy archive names are case-insensitive; canonical modern IDs are digits.
    if "/" in base_id:
        archive, number = base_id.rsplit("/", 1)
        base_id = f"{archive.lower()}/{number}"
    version = int(match.group("version") or 1)
    if version < 1:
        raise ValueError("arXiv version must be positive")
    return NormalizedArxivId(base_id=base_id, version=version)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()
