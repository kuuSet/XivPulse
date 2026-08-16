"""Affiliation enrichment using Semantic Scholar and arXiv HTML."""

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


class AffiliationClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        rate_limit_seconds: float = 3.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._client = http_client
        self.timeout = timeout
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.user_agent = user_agent

    async def fetch(self, arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
        clean = list(
            dict.fromkeys(
                parse_arxiv_id(value)[0]
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
            )
        )[:100]
        if not clean:
            return {}
        if self._client is not None:
            return await self._fetch(clean, self._client)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await self._fetch(clean, client)

    async def _fetch(
        self,
        arxiv_ids: list[str],
        client: httpx.AsyncClient,
    ) -> dict[str, list[str]]:
        try:
            affiliations = await self._fetch_semantic_scholar(arxiv_ids, client)
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            LOG.warning("Semantic Scholar affiliation enrichment failed: %s", exc)
            affiliations = {}

        missing = [arxiv_id for arxiv_id in arxiv_ids if arxiv_id not in affiliations]
        if missing:
            affiliations.update(await self._fetch_arxiv_html(missing[:6], client))
        return affiliations

    async def _fetch_semantic_scholar(
        self,
        arxiv_ids: list[str],
        client: httpx.AsyncClient,
    ) -> dict[str, list[str]]:
        response = await client.post(
            SEMANTIC_SCHOLAR_BATCH_URL,
            params={"fields": "externalIds,authors,authors.affiliations"},
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"ids": [f"ARXIV:{value}" for value in arxiv_ids]},
            timeout=min(self.timeout, 20.0),
        )
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Semantic Scholar response exceeded 10 MiB")
        payload = response.json()
        if not isinstance(payload, list):
            return {}

        expected = set(arxiv_ids)
        result: dict[str, list[str]] = {}
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
            affiliations = list(
                dict.fromkeys(
                    clean_text(value)
                    for author in item.get("authors", [])
                    if isinstance(author, dict)
                    for value in author.get("affiliations", [])
                    if isinstance(value, str) and clean_text(value)
                )
            )
            if affiliations:
                result[arxiv_id] = affiliations
        return result

    async def _fetch_arxiv_html(
        self,
        arxiv_ids: list[str],
        client: httpx.AsyncClient,
    ) -> dict[str, list[str]]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

        async def fetch_one(arxiv_id: str) -> tuple[str, list[str]]:
            await polite_wait(self.rate_limit_seconds)
            url = f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}"
            try:
                async with client.stream(
                    "GET", url, headers=headers, timeout=min(self.timeout, 10.0)
                ) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in limited_bytes(response, _MAX_AFFILIATION_HTML_BYTES):
                        payload.extend(chunk)
            except (DownloadTooLargeError, httpx.HTTPError):
                return arxiv_id, []

            soup = BeautifulSoup(bytes(payload), "html.parser")
            affiliations: list[str] = []
            for element in soup.select(".ltx_authors .ltx_role_affiliation"):
                for label in element.select(".ltx_contact_name"):
                    label.decompose()
                value = clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
                if value and value.casefold() != "affiliation":
                    affiliations.append(value)
            return arxiv_id, list(dict.fromkeys(affiliations))

        fetched = await asyncio.gather(*(fetch_one(arxiv_id) for arxiv_id in arxiv_ids))
        return {arxiv_id: values for arxiv_id, values in fetched if values}
