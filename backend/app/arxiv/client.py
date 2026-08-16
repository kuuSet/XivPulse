"""arXiv metadata and affiliation retrieval."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import date
from urllib.parse import quote

import arxiv
import httpx

from ..domain.paper import Paper
from .affiliations import parse_html_affiliations
from .download import (
    DEFAULT_USER_AGENT,
    DownloadTooLargeError,
    limited_bytes,
    polite_wait,
)
from .results import parse_arxiv_id, to_papers

_PAGE_SIZE = 200
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")
_MAX_AFFILIATION_HTML_BYTES = 64 * 1024 * 1024


class ArxivFetchError(RuntimeError):
    """Raised when arXiv cannot provide a complete, trustworthy result."""


class ArxivClient:
    def __init__(
        self,
        *,
        metadata_client: arxiv.Client | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit_seconds: float = 3.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.user_agent = user_agent
        self._metadata = metadata_client or arxiv.Client(
            page_size=_PAGE_SIZE,
            delay_seconds=self.rate_limit_seconds,
            num_retries=2,
        )
        self._http = http_client

    async def fetch_html_affiliations(self, arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
        """Fetch explicit author affiliations without inspecting paper body text."""

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        clean = list(
            dict.fromkeys(
                normalized.base_id
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
                for normalized in [parse_arxiv_id(value)]
            )
        )

        async def fetch_one(
            client: httpx.AsyncClient, arxiv_id: str
        ) -> tuple[str, list[str]]:
            await polite_wait(self.rate_limit_seconds)
            url = f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}"
            try:
                async with client.stream(
                    "GET", url, headers=headers, timeout=10.0
                ) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in limited_bytes(response, _MAX_AFFILIATION_HTML_BYTES):
                        payload.extend(chunk)
            except (DownloadTooLargeError, httpx.HTTPError):
                return arxiv_id, []
            return arxiv_id, parse_html_affiliations(bytes(payload))

        if self._http is not None:
            fetched = await asyncio.gather(
                *(fetch_one(self._http, arxiv_id) for arxiv_id in clean)
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                fetched = await asyncio.gather(
                    *(fetch_one(client, arxiv_id) for arxiv_id in clean)
                )
        return {arxiv_id: affiliations for arxiv_id, affiliations in fetched if affiliations}

    async def fetch_daily(
        self,
        categories: Sequence[str],
        *,
        target_date: date,
        limit: int,
    ) -> list[Paper]:
        clean = list(dict.fromkeys(value.strip() for value in categories))
        if not clean:
            raise ValueError("At least one valid arXiv category is required")
        invalid = [value for value in clean if not _CATEGORY_RE.fullmatch(value)]
        if invalid:
            raise ValueError("Invalid arXiv categories: " + ", ".join(invalid))
        if limit < 1:
            raise ValueError("arXiv result limit must be positive")

        compact_date = target_date.strftime("%Y%m%d")
        category_query = " OR ".join(f"cat:{category}" for category in clean)
        search = arxiv.Search(
            query=(
                f"({category_query}) AND "
                f"submittedDate:[{compact_date}0000 TO {compact_date}2359]"
            ),
            max_results=limit + 1,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Ascending,
        )
        try:
            results = await asyncio.to_thread(lambda: list(self._metadata.results(search)))
        except Exception as exc:
            raise ArxivFetchError("arXiv metadata request failed") from exc
        if len(results) > limit:
            raise ArxivFetchError(
                f"arXiv results exceed the configured limit {limit} for {target_date}"
            )
        try:
            return to_papers(results, target_date)
        except ValueError as exc:
            raise ArxivFetchError(str(exc)) from exc
