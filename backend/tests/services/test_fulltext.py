"""Full-text download and extraction tests."""

from __future__ import annotations

import httpx
import pytest
from app.arxiv.download import limited_bytes
from app.models import Paper


@pytest.mark.asyncio
async def test_fulltext_prefers_html_and_skips_pdf() -> None:
    from app.services.fulltext import FullTextFetcher

    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        body = f"<article><h1>Paper</h1><p>{'evidence ' * 100}</p></article>"
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = FullTextFetcher(http_client=client, rate_limit_seconds=0)
        result = await fetcher.fetch(Paper(arxiv_id="2608.01234", latest_version=2, title="Paper"))
    assert result.source == "html"
    assert paths == ["/html/2608.01234v2"]
    assert "evidence" in result.content


class _TrackedByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


@pytest.mark.asyncio
async def test_limited_download_stops_reading_as_soon_as_it_exceeds_limit() -> None:
    stream = _TrackedByteStream([b"aaaa", b"bbbb", b"cccc"])
    response = httpx.Response(200, stream=stream)

    with pytest.raises(ValueError, match="response exceeded 6 byte limit"):
        _ = [chunk async for chunk in limited_bytes(response, 6)]

    assert stream.yielded == 2


@pytest.mark.asyncio
async def test_fulltext_streams_larger_pdfs_to_a_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.fulltext import FullTextFetcher

    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.startswith("/html/"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"fake pdf payload")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = FullTextFetcher(http_client=client, rate_limit_seconds=0)

        def extract_pdf(payload) -> str:
            assert not isinstance(payload, bytes | bytearray)
            assert payload.read() == b"fake pdf payload"
            return "evidence " * 100

        monkeypatch.setattr(fetcher, "_extract_pdf", extract_pdf)
        result = await fetcher.fetch(Paper(arxiv_id="2608.01234", latest_version=2, title="Paper"))

    assert fetcher.max_html_bytes == 64 * 1024 * 1024
    assert fetcher.max_pdf_bytes == 100 * 1024 * 1024
    assert result.source == "pdf"
    assert paths == ["/html/2608.01234v2", "/pdf/2608.01234v2"]
