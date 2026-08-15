"""Database access for normalized papers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...domain.paper import Paper, as_utc, deduplicate_papers
from ...models import Paper as StoredPaper
from ...models import PaperVersion, utcnow


def _paper_values(paper: Paper) -> dict[str, object]:
    return {
        "latest_version": paper.version,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors_json": list(paper.authors),
        "affiliations_json": list(paper.affiliations),
        "categories_json": list(paper.categories),
        "primary_category": paper.primary_category,
        "comment": paper.comment,
        "journal_ref": paper.journal_ref,
        "doi": paper.doi,
        "abs_url": paper.abs_url,
        "html_url": paper.html_url,
        "pdf_url": paper.pdf_url,
        "published_at": paper.published_at,
        "arxiv_updated_at": paper.updated_at,
        "ingestion_source": paper.source,
    }


@dataclass(slots=True)
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    versions_added: int = 0
    papers: list[StoredPaper] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.papers)

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "versions_added": self.versions_added,
            "total": self.total,
        }


class PaperRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        papers: Iterable[Paper],
        *,
        seen_at: datetime | None = None,
    ) -> UpsertResult:
        result = UpsertResult()
        timestamp = seen_at or utcnow()
        for paper_data in deduplicate_papers(papers):
            paper_data.canonicalize_urls()
            values = _paper_values(paper_data)
            paper = self.session.scalar(
                select(StoredPaper).where(StoredPaper.arxiv_id == paper_data.arxiv_id)
            )
            created = paper is None
            if paper is None:
                paper = StoredPaper(
                    arxiv_id=paper_data.arxiv_id,
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                    **values,
                )
                self.session.add(paper)
                self.session.flush()
                result.inserted += 1

            existing_version = self.session.scalar(
                select(PaperVersion).where(
                    PaperVersion.paper_id == paper.id,
                    PaperVersion.version == paper_data.version,
                )
            )
            if existing_version is None:
                existing_version = PaperVersion(
                    paper_id=paper.id,
                    version=paper_data.version,
                    arxiv_version_id=paper_data.versioned_id,
                    title=paper_data.title,
                    abstract=paper_data.abstract,
                    authors_json=paper_data.authors,
                    affiliations_json=paper_data.affiliations,
                    categories_json=paper_data.categories,
                    abs_url=paper_data.abs_url,
                    html_url=paper_data.html_url,
                    pdf_url=paper_data.pdf_url,
                    published_at=paper_data.published_at,
                    arxiv_updated_at=paper_data.updated_at,
                    raw_json=paper_data.as_dict(),
                    discovered_at=timestamp,
                )
                self.session.add(existing_version)
                result.versions_added += 1
            elif (
                paper_data.affiliations
                and existing_version.affiliations != paper_data.affiliations
            ):
                existing_version.affiliations = paper_data.affiliations

            should_update = not created and paper_data.version > paper.latest_version
            if paper_data.affiliations and paper.affiliations != paper_data.affiliations:
                paper.affiliations = paper_data.affiliations
            record_updated = as_utc(paper_data.updated_at)
            stored_updated = as_utc(paper.arxiv_updated_at)
            if (
                not created
                and paper_data.version == paper.latest_version
                and record_updated is not None
            ):
                should_update = (
                    should_update or stored_updated is None or record_updated > stored_updated
                )
            if should_update:
                for name, value in values.items():
                    setattr(paper, name, value)
                result.updated += 1
            elif not created:
                result.unchanged += 1
            paper.last_seen_at = timestamp
            result.papers.append(paper)
        self.session.flush()
        return result

    def update_affiliations(self, affiliations_by_id: Mapping[str, Sequence[str]]) -> None:
        if not affiliations_by_id:
            return
        papers = self.session.scalars(
            select(StoredPaper)
            .options(selectinload(StoredPaper.versions))
            .where(StoredPaper.arxiv_id.in_(affiliations_by_id))
        ).all()
        for paper in papers:
            affiliations = list(affiliations_by_id[paper.arxiv_id])
            paper.affiliations = affiliations
            latest = max(paper.versions, key=lambda version: version.version, default=None)
            if latest is not None:
                latest.affiliations = affiliations
        self.session.flush()
