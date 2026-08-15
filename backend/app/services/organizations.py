"""Conservative institution matching based only on supplied affiliations."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

_WATCHED_ORGANIZATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OpenAI", ("openai",)),
    (
        "Meta AI / FAIR",
        (
            "meta ai",
            "meta fair",
            "meta platforms",
            "facebook ai research",
            "facebook artificial intelligence research",
            "fair at meta",
        ),
    ),
    ("Google DeepMind", ("google deepmind", "deepmind")),
    (
        "Google Research",
        ("google research", "google brain", "google llc", "google inc"),
    ),
    ("NVIDIA", ("nvidia",)),
    ("Microsoft Research", ("microsoft research", "microsoft corporation")),
    ("Anthropic", ("anthropic",)),
    ("Apple ML Research", ("apple machine learning", "apple ml research", "apple inc")),
    (
        "Amazon / AWS AI",
        (
            "amazon artificial intelligence",
            "amazon ai",
            "amazon web services",
            "aws ai labs",
        ),
    ),
    ("Tesla AI", ("tesla ai", "tesla, inc", "tesla inc")),
    ("Toyota Research Institute", ("toyota research institute",)),
    ("Physical Intelligence", ("physical intelligence",)),
    ("Allen Institute for AI (AI2)", ("allen institute for ai", "ai2")),
    ("ByteDance Research", ("bytedance research", "bytedance seed", "bytedance inc")),
    ("Tencent AI Lab", ("tencent ai lab", "tencent robotics x")),
    ("Alibaba / DAMO", ("alibaba damo", "damo academy", "alibaba group")),
    ("Huawei Noah's Ark Lab", ("huawei noah", "noah s ark lab", "huawei technologies")),
    ("Shanghai AI Lab", ("shanghai artificial intelligence laboratory", "shanghai ai lab")),
)


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()


def watched_organizations(affiliations: Iterable[object]) -> list[str]:
    """Map explicit affiliation strings to a small, auditable watchlist."""

    text = " | ".join(_normalized(value) for value in affiliations if str(value).strip())
    if not text:
        return []
    return [
        name
        for name, aliases in _WATCHED_ORGANIZATIONS
        if any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(_normalized(alias))}(?![a-z0-9])",
                text,
            )
            for alias in aliases
        )
    ]


def paper_organizations(paper: object | Mapping[str, object]) -> list[str]:
    if isinstance(paper, Mapping):
        values = paper.get("affiliations", paper.get("affiliations_json", []))
    else:
        values = getattr(paper, "affiliations", getattr(paper, "affiliations_json", []))
    return watched_organizations(values if isinstance(values, list | tuple | set) else [])


__all__ = ["paper_organizations", "watched_organizations"]
