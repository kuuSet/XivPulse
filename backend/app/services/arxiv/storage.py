"""Persistence of normalized arXiv records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Paper, PaperVersion, utcnow
from .records import ArxivRecord, as_utc, deduplicate_records


@dataclass(slots=True)
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    versions_added: int = 0
    papers: list[Paper] = field(default_factory=list)

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


def upsert_papers(
    session: Session,
    records: Iterable[ArxivRecord],
    *,
    seen_at: datetime | None = None,
) -> UpsertResult:
    result = UpsertResult()
    timestamp = seen_at or utcnow()
    for record in deduplicate_records(records):
        record.canonicalize_urls()
        paper = session.scalar(select(Paper).where(Paper.arxiv_id == record.arxiv_id))
        created = paper is None
        if paper is None:
            # Fill all non-null columns before flushing for the generated ID.
            paper = Paper(
                arxiv_id=record.arxiv_id,
                latest_version=record.version,
                title=record.title,
                abstract=record.abstract,
                authors_json=list(record.authors),
                affiliations_json=list(record.affiliations),
                categories_json=list(record.categories),
                primary_category=record.primary_category,
                comment=record.comment,
                journal_ref=record.journal_ref,
                doi=record.doi,
                abs_url=record.abs_url,
                html_url=record.html_url,
                pdf_url=record.pdf_url,
                published_at=record.published_at,
                arxiv_updated_at=record.updated_at,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                ingestion_source=record.source,
            )
            session.add(paper)
            session.flush()
            result.inserted += 1

        existing_version = session.scalar(
            select(PaperVersion).where(
                PaperVersion.paper_id == paper.id,
                PaperVersion.version == record.version,
            )
        )
        if existing_version is None:
            existing_version = PaperVersion(
                paper_id=paper.id,
                version=record.version,
                arxiv_version_id=record.versioned_id,
                title=record.title,
                abstract=record.abstract,
                authors_json=record.authors,
                affiliations_json=record.affiliations,
                categories_json=record.categories,
                abs_url=record.abs_url,
                html_url=record.html_url,
                pdf_url=record.pdf_url,
                published_at=record.published_at,
                arxiv_updated_at=record.updated_at,
                raw_json=record.json_dict(),
                discovered_at=timestamp,
            )
            session.add(existing_version)
            result.versions_added += 1
        elif record.affiliations and existing_version.affiliations != record.affiliations:
            existing_version.affiliations = record.affiliations

        should_update = created or record.version > paper.latest_version
        if record.affiliations and paper.affiliations != record.affiliations:
            paper.affiliations = record.affiliations
        record_updated = as_utc(record.updated_at)
        stored_updated = as_utc(paper.arxiv_updated_at)
        if record.version == paper.latest_version and record_updated is not None:
            should_update = (
                should_update or stored_updated is None or record_updated > stored_updated
            )
        if should_update:
            paper.latest_version = record.version
            paper.title = record.title
            paper.abstract = record.abstract
            paper.authors_json = list(record.authors)
            paper.affiliations_json = list(record.affiliations)
            paper.categories_json = list(record.categories)
            paper.primary_category = record.primary_category
            paper.comment = record.comment
            paper.journal_ref = record.journal_ref
            paper.doi = record.doi
            paper.abs_url = record.abs_url
            paper.html_url = record.html_url
            paper.pdf_url = record.pdf_url
            paper.published_at = record.published_at
            paper.arxiv_updated_at = record.updated_at
            paper.ingestion_source = record.source
            if not created:
                result.updated += 1
        elif not created:
            result.unchanged += 1
        paper.last_seen_at = timestamp
        result.papers.append(paper)
    session.flush()
    return result
