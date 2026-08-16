"""Rate-limited external metadata retrieval for arXiv papers."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from urllib.parse import quote

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_exponential

from ..domain.paper import Paper, as_utc, deduplicate_papers
from .parse.atom import parse_arxiv_feed
from .parse.html import parse_html_affiliations
from .parse.normalization import parse_arxiv_id

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"
_MAX_REQUEST_ATTEMPTS = 3
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRY_DELAY_SECONDS = 30.0
_PAGE_SIZE = 200
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")
_EXPONENTIAL_RETRY_WAIT = wait_exponential(max=_MAX_RETRY_DELAY_SECONDS)


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


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, httpx.TransportError)):
        return True
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code in _TRANSIENT_STATUS_CODES
    )


def _retry_wait(state: RetryCallState) -> float:
    error = state.outcome.exception() if state.outcome is not None else None
    retry_after = (
        error.response.headers.get("Retry-After", "").strip()
        if isinstance(error, httpx.HTTPStatusError)
        else ""
    )
    if retry_after.isdigit():
        return min(float(retry_after), _MAX_RETRY_DELAY_SECONDS)
    return _EXPONENTIAL_RETRY_WAIT(state)


def build_date_query(target_date: date, categories: Sequence[str]) -> str:
    compact = target_date.strftime("%Y%m%d")
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    return f"({category_query}) AND submittedDate:[{compact}0000 TO {compact}2359]"


def _validate_page_size(
    papers: Sequence[Paper],
    *,
    offset: int,
    expected_size: int,
) -> None:
    if len(papers) != expected_size:
        raise ArxivFetchError(
            f"arXiv returned an incomplete page at offset {offset}: "
            f"expected {expected_size}, received {len(papers)}"
        )


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
        http_client: httpx.AsyncClient | None = None,
        rate_limit_seconds: float = 3.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._client = http_client
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.timeout = timeout
        self.user_agent = user_agent

    async def _get(
        self,
        url: str,
        *,
        accept: str,
        request_timeout: float | None = None,
        params: dict[str, str | int] | None = None,
    ) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
        }

        async def request() -> httpx.Response:
            await polite_wait(self.rate_limit_seconds)
            if self._client is not None:
                response = await self._client.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            else:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    response = await client.get(
                        url, headers=headers, params=params, timeout=self.timeout
                    )
            response.raise_for_status()
            return response

        async def timed_request() -> httpx.Response:
            if request_timeout is None:
                return await request()
            async with asyncio.timeout(max(1.0, request_timeout)):
                return await request()

        try:
            response = await AsyncRetrying(
                stop=stop_after_attempt(_MAX_REQUEST_ATTEMPTS),
                wait=_retry_wait,
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            )(timed_request)
        except (TimeoutError, httpx.TransportError) as exc:
            raise ArxivFetchError(
                f"arXiv request failed after {_MAX_REQUEST_ATTEMPTS} attempts"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ArxivFetchError(
                f"arXiv request failed with HTTP {exc.response.status_code}"
            ) from exc
        if len(response.content) > 20 * 1024 * 1024:
            raise ArxivFetchError("arXiv response exceeded 20 MiB")
        return response.content

    async def _fetch_page(
        self,
        query: str,
        *,
        start: int,
        size: int,
    ) -> tuple[int, list[Paper]]:
        payload = await self._get(
            ARXIV_API_URL,
            accept="application/atom+xml,application/xml",
            params={
                "search_query": query,
                "start": start,
                "max_results": size,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            },
        )
        try:
            return parse_arxiv_feed(payload)
        except ValueError as exc:
            raise ArxivFetchError("arXiv returned an invalid Atom response") from exc

    async def _fetch_pages(
        self,
        query: str,
        limit: int,
        target_date: date,
    ) -> list[Paper]:
        first_size = min(_PAGE_SIZE, limit)
        total, entries = await self._fetch_page(query, start=0, size=first_size)
        if total > limit:
            raise ArxivFetchError(
                f"arXiv reports {total} papers for {target_date}, exceeding the configured "
                f"limit {limit}"
            )
        _validate_page_size(
            entries,
            offset=0,
            expected_size=min(total, first_size),
        )

        for start in range(_PAGE_SIZE, total, _PAGE_SIZE):
            size = min(_PAGE_SIZE, total - start)
            page_total, page = await self._fetch_page(query, start=start, size=size)
            if page_total != total:
                raise ArxivFetchError(
                    f"arXiv result count changed during pagination: {total} to {page_total}"
                )
            _validate_page_size(page, offset=start, expected_size=size)
            entries.extend(page)
        return entries

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
                payload = await self._get(
                    f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}",
                    accept="text/html,application/xhtml+xml",
                    request_timeout=10.0,
                )
            except ArxivFetchError:
                return arxiv_id, []
            return arxiv_id, parse_html_affiliations(payload)

        # polite_wait serializes request starts globally at the configured
        # interval; concurrent tasks avoid adding slow response times in series.
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
        query = build_date_query(target_date, clean)
        entries = await self._fetch_pages(query, limit, target_date)
        return _prepare_papers(entries, target_date)
