"""Parsing and normalization for arXiv identifiers and metadata payloads."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from ..domain.paper import Paper

_ARXIV_ID_RE = re.compile(
    r"(?P<id>(?:[a-z][a-z.\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NormalizedArxivId:
    base_id: str
    version: int = 1

def parse_arxiv_id(value: str) -> NormalizedArxivId:
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
    return NormalizedArxivId(base_id=base_id, version=version)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_html_affiliations(payload: str | bytes) -> list[str]:
    """Extract only explicitly marked affiliations from an arXiv HTML author block."""

    soup = BeautifulSoup(payload, "html.parser")
    values: list[str] = []
    for element in soup.select(".ltx_authors .ltx_role_affiliation"):
        for label in element.select(".ltx_contact_name"):
            label.decompose()
        value = clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
        if value and value.casefold() != "affiliation":
            values.append(value)
    return list(dict.fromkeys(values))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return clean_text(child.text)
    return None


def _parse_atom_entry(entry: ET.Element) -> Paper:
    raw_id = _child_text(entry, "id")
    title = _child_text(entry, "title")
    published = _child_text(entry, "published")
    if not raw_id or not title or not published:
        raise ValueError("arXiv entry is missing id, title, or published timestamp")
    normalized = parse_arxiv_id(raw_id)
    authors: list[str] = []
    affiliations: list[str] = []
    categories: list[str] = []
    primary_category: str | None = None
    abs_url = ""
    pdf_url = ""
    for child in entry:
        name = _local_name(child.tag)
        if name == "author":
            author = _child_text(child, "name")
            if author:
                authors.append(author)
            affiliations.extend(
                value
                for item in child
                if _local_name(item.tag) == "affiliation" and (value := clean_text(item.text))
            )
        elif name == "category" and (category := child.attrib.get("term", "").strip()):
            categories.append(category)
        elif name == "primary_category":
            primary_category = child.attrib.get("term") or primary_category
        elif name == "link" and (href := child.attrib.get("href")):
            if child.attrib.get("rel") == "alternate":
                abs_url = href
            if (
                child.attrib.get("type") == "application/pdf"
                or child.attrib.get("title") == "pdf"
            ):
                pdf_url = href
    paper = Paper(
        arxiv_id=normalized.base_id,
        version=normalized.version,
        title=title,
        abstract=_child_text(entry, "summary") or "",
        authors=list(dict.fromkeys(authors)),
        affiliations=list(dict.fromkeys(affiliations)),
        categories=list(dict.fromkeys(categories)),
        primary_category=primary_category or (categories[0] if categories else None),
        comment=_child_text(entry, "comment") or None,
        journal_ref=_child_text(entry, "journal_ref") or None,
        doi=_child_text(entry, "doi") or None,
        abs_url=abs_url,
        pdf_url=pdf_url,
        published_at=_parse_datetime(published),
        updated_at=_parse_datetime(_child_text(entry, "updated") or published),
        source="atom",
    )
    paper.canonicalize_urls()
    return paper


def parse_arxiv_feed(payload: str | bytes) -> tuple[int, list[Paper]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("Invalid arXiv Atom response") from exc
    total_text = next(
        (
            clean_text(element.text)
            for element in root.iter()
            if _local_name(element.tag) == "totalResults"
        ),
        "",
    )
    try:
        total = int(total_text)
    except ValueError as exc:
        raise ValueError("arXiv response is missing a valid totalResults") from exc
    papers = [
        _parse_atom_entry(element)
        for element in root
        if _local_name(element.tag) == "entry"
    ]
    return total, papers
