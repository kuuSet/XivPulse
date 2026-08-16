"""arXiv metadata and affiliation retrieval."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from urllib.parse import quote

import arxiv
import httpx

from ..domain.paper import Paper, as_utc, deduplicate_papers
from .affiliations import parse_html_affiliations
from .download import DownloadTooLargeError, limited_bytes
from .identifier import clean_text, parse_arxiv_id

DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"
_PAGE_SIZE = 200
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")
_MAX_AFFILIATION_HTML_BYTES = 64 * 1024 * 1024


class ArxivFetchError(RuntimeError):
    """Raised when arXiv cannot provide a complete, trustworthy result."""


class _GlobalStartRateLimiter:
    """Reserve globally spaced request start times without holding an async lock."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next_start = 0.0

    async def wait(self, interval: float | None = None) -> None:
        spacing = self.interval if interval is None else max(0.0, interval)
        if spacing <= 0:
            return
        with self._lock:
            now = time.monotonic()
            reserved = max(now, self._next_start)
            self._next_start = reserved + spacing
        delay = reserved - now
        if delay > 0:
            await asyncio.sleep(delay)


_GLOBAL_LIMITER = _GlobalStartRateLimiter(3.0)


async def polite_wait(interval: float = 3.0) -> None:
    await _GLOBAL_LIMITER.wait(interval)


def build_date_query(target_date: date, categories: Sequence[str]) -> str:
    compact = target_date.strftime("%Y%m%d")
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    return f"({category_query}) AND submittedDate:[{compact}0000 TO {compact}2359]"


def _paper_from_result(result: arxiv.Result) -> Paper:
    normalized = parse_arxiv_id(result.get_short_id())
    paper = Paper(
        arxiv_id=normalized.base_id,
        version=normalized.version,
        title=clean_text(result.title),
        abstract=clean_text(result.summary),
        authors=list(dict.fromkeys(clean_text(author.name) for author in result.authors)),
        affiliations=list(
            dict.fromkeys(
                affiliation
                for author in result.authors
                for value in author.affiliation
                if (affiliation := clean_text(value))
            )
        ),
        categories=list(dict.fromkeys(result.categories)),
        primary_category=result.primary_category or None,
        comment=clean_text(result.comment) or None,
        journal_ref=clean_text(result.journal_ref) or None,
        doi=clean_text(result.doi) or None,
        abs_url=result.entry_id,
        pdf_url=result.pdf_url or "",
        published_at=as_utc(result.published),
        updated_at=as_utc(result.updated),
        source="atom",
    )
    paper.canonicalize_urls()
    return paper


def _prepare_papers(entries: Sequence[Paper], target_date: date) -> list[Paper]:
    papers = deduplicate_papers(entries)
    outside_date = [
        paper.arxiv_id
        for paper in papers
        if paper.published_at is None or paper.published_at.date() != target_date
    ]
    if outside_date:
        raise ArxivFetchError(
            f"arXiv returned papers outside requested date {target_date}: "
            + ", ".join(outside_date[:3])
        )
    papers.sort(
        key=lambda item: (
            as_utc(item.published_at) or datetime.min.replace(tzinfo=UTC),
            item.arxiv_id,
            item.version,
        )
    )
    return papers


class ArxivClient:
    def __init__(
        self,
        *,
        metadata_client: arxiv.Client | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit_seconds: float = 3.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.timeout = timeout
        self.user_agent = user_agent
        self._metadata = metadata_client or arxiv.Client(
            page_size=_PAGE_SIZE,
            delay_seconds=self.rate_limit_seconds,
            num_retries=2,
        )
        self._http = http_client

    async def _get_html(self, url: str) -> bytes:
        await polite_wait(self.rate_limit_seconds)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

        async def read(client: httpx.AsyncClient) -> bytes:
            async with client.stream(
                "GET", url, headers=headers, timeout=10.0
            ) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in limited_bytes(response, _MAX_AFFILIATION_HTML_BYTES):
                    payload.extend(chunk)
                return bytes(payload)

        if self._http is not None:
            return await read(self._http)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await read(client)

    async def fetch_html_affiliations(self, arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
        """Fetch explicit author affiliations without inspecting paper body text."""

        clean = list(
            dict.fromkeys(
                normalized.base_id
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
                for normalized in [parse_arxiv_id(value)]
            )
        )

        async def fetch_one(arxiv_id: str) -> tuple[str, list[str]]:
            try:
                payload = await self._get_html(
                    f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}"
                )
            except (DownloadTooLargeError, httpx.HTTPError):
                return arxiv_id, []
            return arxiv_id, parse_html_affiliations(payload)

        fetched = await asyncio.gather(*(fetch_one(arxiv_id) for arxiv_id in clean))
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

        search = arxiv.Search(
            query=build_date_query(target_date, clean),
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
        return _prepare_papers([_paper_from_result(result) for result in results], target_date)
