"""Rate-limited external metadata retrieval for arXiv papers."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Sequence
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from ...models import Topic
from .parsing import parse_arxiv_feed, parse_arxiv_id, parse_html_affiliations
from .records import ArxivRecord

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"
_MAX_REQUEST_ATTEMPTS = 3
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRY_DELAY_SECONDS = 30.0
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")


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

    def reset(self) -> None:
        with self._lock:
            self._next_start = 0.0


_GLOBAL_LIMITER = _GlobalStartRateLimiter(3.0)


async def polite_wait(interval: float = 3.0) -> None:
    await _GLOBAL_LIMITER.wait(interval)


def _retry_delay(attempt: int, response: httpx.Response | None = None) -> float:
    retry_after = response.headers.get("Retry-After", "").strip() if response else ""
    if retry_after.isdigit():
        return min(float(retry_after), _MAX_RETRY_DELAY_SECONDS)
    return min(float(2**attempt), _MAX_RETRY_DELAY_SECONDS)


def build_date_query(target_date: date, categories: Sequence[str]) -> str:
    compact = target_date.strftime("%Y%m%d")
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    return f"({category_query}) AND submittedDate:[{compact}0000 TO {compact}2359]"


def _topic_attr(topic: Topic | dict[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(topic, dict):
        if name in topic:
            return topic[name]
        return topic.get(f"{name}_json", default)
    return getattr(topic, name, getattr(topic, f"{name}_json", default))


def build_profile_query(topics: Sequence[Topic | dict[str, Any]]) -> str:
    clauses: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not _topic_attr(topic, "enabled", True):
            continue
        categories = _topic_attr(topic, "arxiv_categories", []) or []
        categories = [
            value for value in categories if isinstance(value, str) and _CATEGORY_RE.match(value)
        ]
        query = str(_topic_attr(topic, "query", "") or "").strip()
        keywords = _topic_attr(topic, "keywords", []) or []
        if query:
            interest = f"({query})"
        else:
            terms = [str(value).strip() for value in keywords if str(value).strip()]
            interest = " OR ".join(f'all:"{term.replace(chr(34), "")}"' for term in terms)
            interest = f"({interest})" if interest else ""
        category_clause = " OR ".join(f"cat:{value}" for value in categories)
        if category_clause and interest:
            clause = f"(({category_clause}) AND {interest})"
        elif category_clause:
            clause = f"({category_clause})"
        else:
            clause = interest
        key = clause.casefold()
        if clause and key not in seen:
            seen.add(key)
            clauses.append(clause)
    return " OR ".join(clauses) or "cat:cs.RO"


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
            if self._client is not None:
                return await self._client.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                return await client.get(url, headers=headers, params=params, timeout=self.timeout)

        for attempt in range(_MAX_REQUEST_ATTEMPTS):
            await polite_wait(self.rate_limit_seconds)
            try:
                if request_timeout is None:
                    response = await request()
                else:
                    async with asyncio.timeout(max(1.0, request_timeout)):
                        response = await request()
            except (TimeoutError, httpx.TransportError):
                if attempt + 1 == _MAX_REQUEST_ATTEMPTS:
                    raise
                await asyncio.sleep(_retry_delay(attempt))
                continue
            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt + 1 < _MAX_REQUEST_ATTEMPTS
            ):
                await asyncio.sleep(_retry_delay(attempt, response))
                continue
            response.raise_for_status()
            if len(response.content) > 20 * 1024 * 1024:
                raise ValueError("arXiv feed response exceeded 20 MiB")
            return response.content
        raise RuntimeError("arXiv request retry loop ended unexpectedly")

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
            except (TimeoutError, httpx.HTTPError, ValueError):
                return arxiv_id, []
            return arxiv_id, parse_html_affiliations(payload)

        # polite_wait serializes request starts globally at the configured
        # interval; concurrent tasks avoid adding slow response times in series.
        fetched = await asyncio.gather(*(fetch_one(arxiv_id) for arxiv_id in clean))
        return {arxiv_id: affiliations for arxiv_id, affiliations in fetched if affiliations}

    async def fetch_daily(
        self,
        topics: Sequence[Topic | dict[str, Any]],
        *,
        target_date: date,
        max_results: int = 100,
    ) -> list[ArxivRecord]:
        categories = [
            category
            for topic in topics
            if _topic_attr(topic, "enabled", True)
            for category in (_topic_attr(topic, "arxiv_categories", []) or [])
        ]
        clean = list(
            dict.fromkeys(
                value.strip()
                for value in categories
                if isinstance(value, str) and _CATEGORY_RE.match(value.strip())
            )
        )
        if not clean:
            raise ValueError("At least one valid arXiv category is required")
        limit = min(max(1, int(max_results)), 2000)
        payload = await self._get(
            ARXIV_API_URL,
            accept="application/atom+xml,application/xml",
            params={
                "search_query": build_date_query(target_date, clean),
                "start": 0,
                "max_results": limit,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            },
        )
        total, records = parse_arxiv_feed(payload)
        if total > limit:
            raise ValueError(
                f"arXiv reports {total} papers for {target_date}, exceeding the configured "
                f"limit {limit}"
            )
        outside_date = [
            record.arxiv_id
            for record in records
            if record.published_at is None or record.published_at.date() != target_date
        ]
        if outside_date:
            raise ValueError(
                f"arXiv returned papers outside requested date {target_date}: "
                + ", ".join(outside_date[:3])
            )
        return records
