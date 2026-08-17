"""Research-direction catalog presentation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
CATALOG_VERSION = str(_CATALOG["version"])


def _direction(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "aliases": [],
        "context_keywords": [],
        "negative_keywords": [],
        "weight": 1.0,
        "enabled": True,
        "level": "main",
        "parent_slug": None,
        **value,
    }


DIRECTION_CATALOG = tuple(_direction(item) for item in _CATALOG["items"])


def direction_catalog_payload() -> dict[str, Any]:
    groups = list(dict.fromkeys(item["group"] for item in DIRECTION_CATALOG))
    by_slug = {item["slug"]: item for item in DIRECTION_CATALOG}
    items = []
    for catalog_item in DIRECTION_CATALOG:
        item = dict(catalog_item)
        parent = by_slug.get(str(item.get("parent_slug") or ""))
        item["parent_name"] = parent["name"] if parent is not None else None
        items.append(item)
    return {
        "version": CATALOG_VERSION,
        "updated_at": _CATALOG["updated_at"],
        "groups": groups,
        "count": len(DIRECTION_CATALOG),
        "main_count": sum(item["level"] == "main" for item in DIRECTION_CATALOG),
        "specialty_count": sum(item["level"] == "specialty" for item in DIRECTION_CATALOG),
        "items": items,
        "sources": _CATALOG["sources"],
        "scope_note": _CATALOG["scope_note"],
    }


__all__ = [
    "CATALOG_VERSION",
    "DIRECTION_CATALOG",
    "direction_catalog_payload",
]
