"""Project-paper tag normalization."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _clean_tags(value: Any, *, limit: int = 50) -> list[str]:
    """Keep the first spelling while collapsing visually equivalent user tags."""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        if not isinstance(item, str):
            continue
        cleaned = unicodedata.normalize("NFKC", item).strip()
        key = re.sub(r"[\s_-]+", "", cleaned).casefold()
        if cleaned and key and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
