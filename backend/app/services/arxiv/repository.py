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
            paper = self.session.scalar(
                select(StoredPaper).where(StoredPaper.arxiv_id == paper_data.arxiv_id)
            )
            created = paper is None
            if paper is None:
                # Fill all non-null columns before flushing for the generated ID.
                paper = StoredPaper(
                    arxiv_id=paper_data.arxiv_id,
                    latest_version=paper_data.version,
                    title=paper_data.title,
                    abstract=paper_data.abstract,
                    authors_json=list(paper_data.authors),
                    affiliations_json=list(paper_data.affiliations),
                    categories_json=list(paper_data.categories),
                    primary_category=paper_data.primary_category,
                    comment=paper_data.comment,
                    journal_ref=paper_data.journal_ref,
                    doi=paper_data.doi,
                    abs_url=paper_data.abs_url,
                    html_url=paper_data.html_url,
                    pdf_url=paper_data.pdf_url,
                    published_at=paper_data.published_at,
                    arxiv_updated_at=paper_data.updated_at,
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                    ingestion_source=paper_data.source,
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

            should_update = created or paper_data.version > paper.latest_version
            if paper_data.affiliations and paper.affiliations != paper_data.affiliations:
                paper.affiliations = paper_data.affiliations
            record_updated = as_utc(paper_data.updated_at)
            stored_updated = as_utc(paper.arxiv_updated_at)
            if paper_data.version == paper.latest_version and record_updated is not None:
                should_update = (
                    should_update or stored_updated is None or record_updated > stored_updated
                )
            if should_update:
                paper.latest_version = paper_data.version
                paper.title = paper_data.title
                paper.abstract = paper_data.abstract
                paper.authors_json = list(paper_data.authors)
                paper.affiliations_json = list(paper_data.affiliations)
                paper.categories_json = list(paper_data.categories)
                paper.primary_category = paper_data.primary_category
                paper.comment = paper_data.comment
                paper.journal_ref = paper_data.journal_ref
                paper.doi = paper_data.doi
                paper.abs_url = paper_data.abs_url
                paper.html_url = paper_data.html_url
                paper.pdf_url = paper_data.pdf_url
                paper.published_at = paper_data.published_at
                paper.arxiv_updated_at = paper_data.updated_at
                paper.ingestion_source = paper_data.source
                if not created:
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
