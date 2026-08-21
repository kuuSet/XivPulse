"""arXiv affiliation-enrichment tests."""

import json

import httpx
import pytest
from app.arxiv.affiliations import fetch_affiliations


@pytest.mark.asyncio
async def test_affiliation_fetch_falls_back_to_arxiv_author_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json=[])
        assert request.url.path == "/html/2608.01234"
        return httpx.Response(
            200,
            text=(
                '<div class="ltx_authors"><span class="ltx_role_affiliation">'
                '<span class="ltx_contact_name">Affiliation:</span>Meta AI</span></div>'
                "<article>NVIDIA baseline.</article>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        values = await fetch_affiliations(
            ["2608.01234v2"], http_client=client, rate_limit_seconds=0
        )

    assert values == {"2608.01234": ["Meta AI"]}


@pytest.mark.asyncio
async def test_affiliation_html_has_an_independent_streaming_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.arxiv import affiliations as affiliations_module

    monkeypatch.setattr(affiliations_module, "_MAX_AFFILIATION_HTML_BYTES", 8)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(200, json=[])
        return httpx.Response(200, content=b"x" * 9)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        values = await fetch_affiliations(
            ["2608.01234"],
            http_client=client,
            rate_limit_seconds=0,
        )

    assert values == {}


@pytest.mark.asyncio
async def test_semantic_scholar_affiliations_require_exact_arxiv_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"ids": ["ARXIV:2608.01234"]}
        return httpx.Response(
            200,
            json=[
                {
                    "externalIds": {"ArXiv": "2608.01234"},
                    "authors": [
                        {"affiliations": ["Google DeepMind"]},
                        {"affiliations": ["University College London"]},
                    ],
                },
                {
                    "externalIds": {"ArXiv": "2608.99999"},
                    "authors": [{"affiliations": ["OpenAI"]}],
                },
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        values = await fetch_affiliations(["2608.01234v2"], http_client=client)

    assert values == {"2608.01234": ["Google DeepMind", "University College London"]}
