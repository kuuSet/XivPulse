"""Read-only followed-topic presets and initial selections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA = json.loads(Path(__file__).with_name("presets.json").read_text(encoding="utf-8"))
PRESET_VERSION = str(_DATA["version"])
INTEREST_PRESETS: tuple[dict[str, Any], ...] = tuple(_DATA["items"])


def interest_presets_payload() -> dict[str, Any]:
    return {
        "version": PRESET_VERSION,
        "items": [
            {key: value for key, value in preset.items() if key != "default_order"}
            for preset in INTEREST_PRESETS
        ],
    }


def initial_topic_values() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "name": preset["name"],
            "description": preset["description"],
            "aliases_json": list(preset["aliases"]),
            "arxiv_categories_json": list(preset["arxiv_categories"]),
        }
        for preset in sorted(
            (item for item in INTEREST_PRESETS if "default_order" in item),
            key=lambda item: item["default_order"],
        )
    )


__all__ = [
    "INTEREST_PRESETS",
    "PRESET_VERSION",
    "initial_topic_values",
    "interest_presets_payload",
]
