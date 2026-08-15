"""Paper metadata independent of transport and database concerns."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote


@dataclass(slots=True)
class Paper:
    arxiv_id: str
    version: int
    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_category: str | None = None
    comment: str | None = None
    journal_ref: str | None = None
    doi: str | None = None
    abs_url: str = ""
    html_url: str = ""
    pdf_url: str = ""
    published_at: datetime | None = None
    updated_at: datetime | None = None
    source: str = "atom"

    @property
    def versioned_id(self) -> str:
        return f"{self.arxiv_id}v{self.version}"

    def canonicalize_urls(self) -> None:
        versioned = quote(self.versioned_id, safe="/.")
        if not self.abs_url:
            self.abs_url = f"https://arxiv.org/abs/{versioned}"
        if not self.html_url:
            self.html_url = f"https://arxiv.org/html/{versioned}"
        if not self.pdf_url:
            self.pdf_url = f"https://arxiv.org/pdf/{versioned}"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("published_at", "updated_at"):
            if value[key] is not None:
                value[key] = value[key].isoformat()
        return value


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def deduplicate_papers(papers: Iterable[Paper]) -> list[Paper]:
    selected: dict[str, Paper] = {}
    positions: dict[str, int] = {}
    for position, paper in enumerate(papers):
        current = selected.get(paper.arxiv_id)
        current_time = current.updated_at if current else None
        newer = current is None or paper.version > current.version
        if current is not None and paper.version == current.version:
            newer = (as_utc(paper.updated_at) or datetime.min.replace(tzinfo=UTC)) > (
                as_utc(current_time) or datetime.min.replace(tzinfo=UTC)
            )
        if newer:
            selected[paper.arxiv_id] = paper
            positions.setdefault(paper.arxiv_id, position)
    return sorted(selected.values(), key=lambda item: positions[item.arxiv_id])
