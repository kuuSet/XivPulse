"""Enrich affiliations using Semantic Scholar and arXiv HTML."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from .download import (
    DEFAULT_USER_AGENT,
    DownloadTooLargeError,
    limited_bytes,
    polite_wait,
)
from .results import clean_text, parse_arxiv_id

LOG = logging.getLogger("xivpulse")
SEMANTIC_SCHOLAR_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
_MAX_AFFILIATION_HTML_BYTES = 64 * 1024 * 1024


async def fetch_affiliations(
    arxiv_ids: Sequence[str],
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
    rate_limit_seconds: float = 3.0,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, list[str]]:
    clean = list(
        dict.fromkeys(
            parse_arxiv_id(value)[0]
            for value in arxiv_ids
            if isinstance(value, str) and value.strip()
        )
    )[:100]
    if not clean:
        return {}

    client = http_client
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)
    try:
        try:
            response = await client.post(
                SEMANTIC_SCHOLAR_BATCH_URL,
                params={"fields": "externalIds,authors,authors.affiliations"},
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={"ids": [f"ARXIV:{value}" for value in clean]},
                timeout=min(timeout, 20.0),
            )
            response.raise_for_status()
            if len(response.content) > 10 * 1024 * 1024:
                raise ValueError("Semantic Scholar response exceeded 10 MiB")
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Semantic Scholar returned an invalid response")

            expected = set(clean)
            affiliations: dict[str, list[str]] = {}
            for item in payload:
                if not isinstance(item, dict):
                    continue
                external_ids = item.get("externalIds")
                raw_id = external_ids.get("ArXiv") if isinstance(external_ids, dict) else None
                try:
                    arxiv_id, _ = parse_arxiv_id(str(raw_id))
                except ValueError:
                    continue
                if arxiv_id not in expected:
                    continue
                values = list(
                    dict.fromkeys(
                        clean_text(value)
                        for author in item.get("authors", [])
                        if isinstance(author, dict)
                        for value in author.get("affiliations", [])
                        if isinstance(value, str) and clean_text(value)
                    )
                )
                if values:
                    affiliations[arxiv_id] = values
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            LOG.warning("Semantic Scholar affiliation enrichment failed: %s", exc)
            affiliations = {}

        missing = [arxiv_id for arxiv_id in clean if arxiv_id not in affiliations]
        if not missing:
            return affiliations

        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

        async def fetch_one(arxiv_id: str) -> tuple[str, list[str]]:
            await polite_wait(max(0.0, rate_limit_seconds))
            url = f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}"
            try:
                async with client.stream(
                    "GET", url, headers=headers, timeout=min(timeout, 10.0)
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in limited_bytes(response, _MAX_AFFILIATION_HTML_BYTES):
                        body.extend(chunk)
            except (DownloadTooLargeError, httpx.HTTPError):
                return arxiv_id, []

            soup = BeautifulSoup(bytes(body), "html.parser")
            values: list[str] = []
            for element in soup.select(".ltx_authors .ltx_role_affiliation"):
                for label in element.select(".ltx_contact_name"):
                    label.decompose()
                value = clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
                if value and value.casefold() != "affiliation":
                    values.append(value)
            return arxiv_id, list(dict.fromkeys(values))

        fetched = await asyncio.gather(*(fetch_one(arxiv_id) for arxiv_id in missing[:6]))
        affiliations.update(
            {arxiv_id: values for arxiv_id, values in fetched if values}
        )
        return affiliations
    finally:
        if http_client is None:
            await client.aclose()
