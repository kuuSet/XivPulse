"""Affiliation enrichment using external paper metadata sources."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from .identifier import clean_text

if TYPE_CHECKING:
    from .client import ArxivClient
    from .semantic_scholar import SemanticScholarClient

LOG = logging.getLogger("xivpulse")


def parse_html_affiliations(payload: str | bytes) -> list[str]:
    """Extract only explicitly marked affiliations from an arXiv HTML author block."""

    soup = BeautifulSoup(payload, "html.parser")
    values: list[str] = []
    for element in soup.select(".ltx_authors .ltx_role_affiliation"):
        for label in element.select(".ltx_contact_name"):
            label.decompose()
        value = clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
        if value and value.casefold() != "affiliation":
            values.append(value)
    return list(dict.fromkeys(values))


async def enrich_affiliations(
    arxiv_ids: Sequence[str],
    *,
    arxiv_client: ArxivClient,
    semantic_scholar: SemanticScholarClient,
) -> dict[str, list[str]]:
    enriched: dict[str, list[str]] = {}
    if arxiv_ids:
        try:
            enriched.update(await semantic_scholar.fetch_affiliations(arxiv_ids))
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            LOG.warning("Semantic Scholar affiliation enrichment failed: %s", exc)
    missing = [value for value in arxiv_ids if value not in enriched]
    if missing:
        enriched.update(await arxiv_client.fetch_html_affiliations(missing[:6]))
    return enriched
