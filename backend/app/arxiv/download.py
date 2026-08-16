"""Bounded streaming for arXiv responses."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


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
