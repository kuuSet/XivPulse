"""Convert arXiv library results into XivPulse papers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import arxiv

from ..domain.paper import Paper, as_utc, deduplicate_papers
from .identifier import clean_text, parse_arxiv_id


def to_paper(result: arxiv.Result) -> Paper:
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
