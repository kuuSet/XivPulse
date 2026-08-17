"""Research-direction catalog presentation."""

from __future__ import annotations

from typing import Any

from .data import (
    CATALOG_SOURCES,
    CATALOG_VERSION,
    CORE_DIRECTION_CATALOG,
    DIRECTION_CATALOG,
    SPECIALTY_DIRECTION_CATALOG,
)


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
        "updated_at": "2026-08-14",
        "groups": groups,
        "count": len(DIRECTION_CATALOG),
        "main_count": len(CORE_DIRECTION_CATALOG),
        "specialty_count": len(SPECIALTY_DIRECTION_CATALOG),
        "items": items,
        "sources": [dict(source) for source in CATALOG_SOURCES],
        "scope_note": (
            "目录分为稳定主干与精确专题；专题隶属于主干，但添加后仍是独立关注方向。"
            "单一方法、数据集、榜单、论文分区、产品名或短期热词不会成为方向。"
        ),
    }


__all__ = [
    "CATALOG_SOURCES",
    "CATALOG_VERSION",
    "CORE_DIRECTION_CATALOG",
    "DIRECTION_CATALOG",
    "SPECIALTY_DIRECTION_CATALOG",
    "direction_catalog_payload",
]
