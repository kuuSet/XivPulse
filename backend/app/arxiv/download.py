"""Rate-limited, bounded streaming for arXiv responses."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator

import httpx

DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"


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


class DownloadTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"response exceeded {limit} byte limit")
        self.limit = limit


async def limited_bytes(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    content_length = response.headers.get("Content-Length")
    try:
        expected = int(content_length) if content_length is not None else None
    except ValueError:
        expected = None
    if expected is not None and expected > limit:
        raise DownloadTooLargeError(limit)

    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > limit:
            raise DownloadTooLargeError(limit)
        yield chunk
