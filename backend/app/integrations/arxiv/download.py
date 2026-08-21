"""Bound HTTP response streams before storing or parsing them."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"


async def limited_bytes(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    content_length = response.headers.get("Content-Length")
    try:
        expected = int(content_length) if content_length is not None else None
    except ValueError:
        expected = None
    if expected is not None and expected > limit:
        raise ValueError(f"response exceeded {limit} byte limit")

    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > limit:
            raise ValueError(f"response exceeded {limit} byte limit")
        yield chunk
