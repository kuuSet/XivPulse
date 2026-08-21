"""Normalize arXiv results and identifiers into XivPulse papers."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime

import arxiv

from ..domain.paper import Paper, as_utc, deduplicate_papers

_ARXIV_ID_RE = re.compile(
    r"(?P<id>(?:[a-z][a-z.\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)


def parse_arxiv_id(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("arXiv identifier cannot be empty")
    cleaned = html.unescape(value).strip()
    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]
    cleaned = re.sub(r"\.pdf$", "", cleaned, flags=re.IGNORECASE).rstrip("/")
    match = _ARXIV_ID_RE.search(cleaned)
    if match is None:
        raise ValueError(f"Invalid arXiv identifier: {value!r}")
    base_id = match.group("id")
    # Legacy archive names are case-insensitive; canonical modern IDs are digits.
    if "/" in base_id:
        archive, number = base_id.rsplit("/", 1)
        base_id = f"{archive.lower()}/{number}"
    version = int(match.group("version") or 1)
    if version < 1:
        raise ValueError("arXiv version must be positive")
    return base_id, version


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def to_paper(result: arxiv.Result) -> Paper:
    arxiv_id, version = parse_arxiv_id(result.get_short_id())
    paper = Paper(
        arxiv_id=arxiv_id,
        version=version,
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
    )
    paper.canonicalize_urls()
    return paper


def to_papers(results: Sequence[arxiv.Result], target_date: date) -> list[Paper]:
    papers = deduplicate_papers(to_paper(result) for result in results)
    outside_date = [
        paper.arxiv_id
        for paper in papers
        if paper.published_at is None or paper.published_at.date() != target_date
    ]
    if outside_date:
        raise ValueError(
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
