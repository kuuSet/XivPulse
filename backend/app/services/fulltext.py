"""Bounded arXiv HTML and PDF full-text retrieval."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from tempfile import TemporaryFile
from typing import BinaryIO, Literal
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from ..models import Paper
from ..arxiv.download import DEFAULT_USER_AGENT, limited_bytes, polite_wait
from ..arxiv.results import parse_arxiv_id


@dataclass(slots=True)
class FullTextDocument:
    content: str
    source: Literal["html", "pdf", "unavailable"]
    url: str | None
    truncated: bool = False
    error: str | None = None


class FullTextFetcher:
    """Fetch arXiv HTML first and fall back to bounded PDF extraction."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 45.0,
        rate_limit_seconds: float = 3.0,
        max_chars: int = 45_000,
        max_html_bytes: int = 64 * 1024 * 1024,
        max_pdf_bytes: int = 100 * 1024 * 1024,
        max_pdf_pages: int = 40,
    ) -> None:
        self._client = http_client
        self.timeout = timeout
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.max_chars = max(5_000, max_chars)
        self.max_html_bytes = max(1024 * 1024, max_html_bytes)
        self.max_pdf_bytes = max(1024 * 1024, max_pdf_bytes)
        self.max_pdf_pages = max(1, max_pdf_pages)

    @asynccontextmanager
    async def _stream(self, url: str, accept: str) -> AsyncIterator[httpx.Response]:
        await polite_wait(self.rate_limit_seconds)
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": accept}
        if self._client is not None:
            async with self._client.stream(
                "GET", url, headers=headers, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                yield response
            return
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async with client.stream(
                "GET", url, headers=headers, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                yield response

    async def fetch(self, paper: Paper | str) -> FullTextDocument:
        raw_id = paper.arxiv_id if isinstance(paper, Paper) else paper
        normalized = parse_arxiv_id(raw_id)
        if isinstance(paper, Paper):
            version = max(1, int(paper.latest_version or normalized.version))
        else:
            version = normalized.version
        versioned = f"{normalized.base_id}v{version}"
        encoded = quote(versioned, safe="/.")
        html_url = f"https://arxiv.org/html/{encoded}"
        pdf_url = f"https://arxiv.org/pdf/{encoded}"
        errors: list[str] = []

        try:
            async with self._stream(
                html_url, "text/html,application/xhtml+xml"
            ) as response:
                encoding = response.encoding or "utf-8"
                payload = bytearray()
                async for chunk in limited_bytes(response, self.max_html_bytes):
                    payload.extend(chunk)
            content = self._extract_html(payload.decode(encoding, errors="replace"))
            if len(content) >= 500:
                content, truncated = self._truncate(content)
                return FullTextDocument(content, "html", html_url, truncated)
            errors.append("HTML 正文过短")
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            errors.append(f"HTML 不可用 ({type(exc).__name__})")

        try:
            with TemporaryFile() as payload:
                async with self._stream(pdf_url, "application/pdf") as response:
                    async for chunk in limited_bytes(response, self.max_pdf_bytes):
                        payload.write(chunk)
                payload.seek(0)
                content = await asyncio.to_thread(self._extract_pdf, payload)
            if not content.strip():
                raise ValueError("PDF contained no extractable text")
            content, truncated = self._truncate(content)
            return FullTextDocument(content, "pdf", pdf_url, truncated)
        except (httpx.HTTPError, OSError, UnicodeError, ValueError, PyPdfError) as exc:
            errors.append(f"PDF 不可用 ({type(exc).__name__})")
        return FullTextDocument("", "unavailable", None, error="；".join(errors))

    @staticmethod
    def _extract_html(payload: str) -> str:
        soup = BeautifulSoup(payload, "html.parser")
        for element in soup.select("script, style, nav, header, footer, aside, .ltx_page_footer"):
            element.decompose()
        root = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        return re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True)).strip()

    def _extract_pdf(self, payload: BinaryIO) -> str:
        reader = PdfReader(payload)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pypdf raises provider-specific exceptions
                raise ValueError("Encrypted PDF could not be read") from exc
        parts: list[str] = []
        for page in reader.pages[: self.max_pdf_pages]:
            parts.append(page.extract_text() or "")
            if sum(len(part) for part in parts) >= self.max_chars:
                break
        return re.sub(r"\n{3,}", "\n\n", "\n\n".join(parts)).strip()

    def _truncate(self, content: str) -> tuple[str, bool]:
        if len(content) <= self.max_chars:
            return content, False
        return content[: self.max_chars].rsplit("\n", 1)[0].strip(), True
