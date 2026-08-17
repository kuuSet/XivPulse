"""Method catalog matching, discovery, and reviewed local extensions."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..state import clean_string_list


@dataclass(frozen=True)
class MethodTerm:
    slug: str
    group: str
    name: str
    aliases: tuple[str, ...]
    context_terms: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    description: str = ""
    source: str = "built_in"

    def wire(self) -> dict[str, Any]:
        return asdict(self)


def _method(value: Mapping[str, Any]) -> MethodTerm:
    name = str(value["name"])
    return MethodTerm(
        slug=str(value["slug"]),
        group=str(value["group"]),
        name=name,
        aliases=tuple(dict.fromkeys((name, *(str(item) for item in value.get("aliases", []))))),
        context_terms=tuple(str(item) for item in value.get("context_terms", [])),
        exclusions=tuple(str(item) for item in value.get("exclusions", [])),
        description=str(value.get("description", "")),
        source=str(value.get("source", "built_in")),
    )


_CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
METHOD_CATALOG_VERSION = str(_CATALOG["version"])
METHOD_CANDIDATE_WINDOW_DAYS = int(_CATALOG["candidate_discovery"]["window_days"])
METHOD_CANDIDATE_MIN_PAPERS = int(_CATALOG["candidate_discovery"]["min_papers"])
METHOD_CATALOG = tuple(_method(item) for item in _CATALOG["items"])


def normalize_method_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s_\-‐‑‒–—]+", "", text)


def clean_custom_methods(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = normalize_method_key(name)
        if not name or not key or key in seen:
            continue
        result.append(
            {
                "name": name,
                "aliases": clean_string_list(item.get("aliases", []), limit=20),
            }
        )
        seen.add(key)
    return result


def _has_term(text: str, term: str) -> bool:
    phrase = term.strip()
    if not phrase:
        return False
    if re.fullmatch(r"[A-Za-z0-9_+.'-]+", phrase):
        return (
            re.search(rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text, re.I)
            is not None
        )
    return (
        unicodedata.normalize("NFKC", phrase).casefold()
        in unicodedata.normalize("NFKC", text).casefold()
    )


def custom_method_terms(values: Iterable[Mapping[str, Any]] | None) -> tuple[MethodTerm, ...]:
    result: list[MethodTerm] = []
    occupied = {normalize_method_key(alias) for item in METHOD_CATALOG for alias in item.aliases}
    for index, value in enumerate(values or []):
        name = str(value.get("name") or "").strip()
        if not name or normalize_method_key(name) in occupied:
            continue
        aliases = [str(item).strip() for item in value.get("aliases", []) if str(item).strip()]
        unique_aliases = tuple(dict.fromkeys((name, *aliases)))
        keys = {normalize_method_key(alias) for alias in unique_aliases}
        if not keys or keys & occupied:
            continue
        occupied.update(keys)
        result.append(
            MethodTerm(
                slug=f"custom-{index + 1}-{normalize_method_key(name)[:32]}",
                group="自定义方法",
                name=name,
                aliases=unique_aliases,
                description="由用户审核接受的本地方法标签。",
                source="custom",
            )
        )
    return tuple(result)


def catalog_with_custom(
    values: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[MethodTerm, ...]:
    return (*METHOD_CATALOG, *custom_method_terms(values))


def match_method_labels(
    text: str,
    custom_values: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    labels: list[str] = []
    for item in catalog_with_custom(custom_values):
        if item.exclusions and any(_has_term(text, term) for term in item.exclusions):
            continue
        if not any(_has_term(text, alias) for alias in item.aliases):
            continue
        if item.context_terms and not any(_has_term(text, term) for term in item.context_terms):
            continue
        labels.append(item.name)
    return labels


def _candidate_phrase(value: object) -> str | None:
    phrase = unicodedata.normalize("NFKC", str(value or "")).strip(" \t\r\n,，;；")
    phrase = re.sub(r"\s+", " ", phrase)
    if not 2 <= len(phrase) <= 80:
        return None
    if any(mark in phrase for mark in ("。", "！", "？", "；", "\n", ": ", "：")):
        return None
    if re.search(r"[.!?;](?:\s|$)", phrase):
        return None
    if len(re.findall(r"[A-Za-z0-9][A-Za-z0-9+.'/-]*", phrase)) > 12:
        return None
    if re.match(r"^(we |our |this |the paper |本文|我们|该文|提出|一种)", phrase, re.I):
        return None
    return phrase


def discover_method_candidates(
    records: Iterable[Mapping[str, Any]],
    *,
    custom_values: Iterable[Mapping[str, Any]] | None = None,
    dismissed: Iterable[str] = (),
    min_papers: int = METHOD_CANDIDATE_MIN_PAPERS,
) -> list[dict[str, Any]]:
    known = {
        normalize_method_key(alias)
        for item in catalog_with_custom(custom_values)
        for alias in item.aliases
    }
    dismissed_keys = {normalize_method_key(item) for item in dismissed}
    papers: defaultdict[str, set[int]] = defaultdict(set)
    display: dict[str, str] = {}
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        paper_id = int(record.get("paper_id") or 0)
        if not paper_id:
            continue
        for raw in record.get("methods", []) or []:
            phrase = _candidate_phrase(raw)
            if not phrase:
                continue
            key = normalize_method_key(phrase)
            if (
                not key
                or key in known
                or key in dismissed_keys
                or match_method_labels(phrase, custom_values)
            ):
                continue
            display.setdefault(key, phrase)
            papers[key].add(paper_id)
            if not any(item["paper_id"] == paper_id for item in examples[key]):
                examples[key].append(
                    {
                        "paper_id": paper_id,
                        "arxiv_id": str(record.get("arxiv_id") or ""),
                        "title": str(record.get("title") or ""),
                    }
                )
    keys = sorted(
        (key for key, paper_ids in papers.items() if len(paper_ids) >= max(2, min_papers)),
        key=lambda key: (-len(papers[key]), display[key].casefold()),
    )
    return [
        {
            "key": key,
            "name": display[key],
            "paper_count": len(papers[key]),
            "paper_ids": sorted(papers[key]),
            "representative_papers": examples[key][:3],
            "status": "pending",
        }
        for key in keys
    ]


def method_catalog_payload(
    custom_values: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    items = catalog_with_custom(custom_values)
    groups = list(dict.fromkeys(item.group for item in items))
    return {
        "version": METHOD_CATALOG_VERSION,
        "count": len(items),
        "built_in_count": len(METHOD_CATALOG),
        "custom_count": len(items) - len(METHOD_CATALOG),
        "groups": groups,
        "items": [item.wire() for item in items],
        "scope_note": _CATALOG["scope_note"],
    }


__all__ = [
    "METHOD_CANDIDATE_MIN_PAPERS",
    "METHOD_CANDIDATE_WINDOW_DAYS",
    "METHOD_CATALOG",
    "METHOD_CATALOG_VERSION",
    "MethodTerm",
    "catalog_with_custom",
    "custom_method_terms",
    "discover_method_candidates",
    "match_method_labels",
    "method_catalog_payload",
    "normalize_method_key",
]
