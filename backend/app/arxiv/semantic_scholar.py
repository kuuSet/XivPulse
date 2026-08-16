"""Semantic Scholar affiliation enrichment for arXiv papers."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from .download import DEFAULT_USER_AGENT
from .results import clean_text, parse_arxiv_id

SEMANTIC_SCHOLAR_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"


class SemanticScholarClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._client = http_client
        self.timeout = timeout
        self.user_agent = user_agent

    async def fetch_affiliations(self, arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
        """Fetch publication-specific affiliations using exact arXiv identifiers."""

        clean = list(
            dict.fromkeys(
                parse_arxiv_id(value)[0]
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
            )
        )[:100]
        if not clean:
            return {}
        params = {"fields": "externalIds,authors,authors.affiliations"}
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            response = await self._client.post(
                SEMANTIC_SCHOLAR_BATCH_URL,
                params=params,
                headers=headers,
                json={"ids": [f"ARXIV:{value}" for value in clean]},
                timeout=min(self.timeout, 20.0),
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.post(
                    SEMANTIC_SCHOLAR_BATCH_URL,
                    params=params,
                    headers=headers,
                    json={"ids": [f"ARXIV:{value}" for value in clean]},
                    timeout=min(self.timeout, 20.0),
                )
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Semantic Scholar response exceeded 10 MiB")
        payload = response.json()
        if not isinstance(payload, list):
            return {}
        expected = set(clean)
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
