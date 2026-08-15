"""Affiliation enrichment using external paper metadata sources."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from .client import ArxivClient
from .semantic_scholar import SemanticScholarClient

LOG = logging.getLogger("xivpulse")


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
