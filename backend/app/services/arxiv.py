"""arXiv fetching, metadata fallbacks, identifier normalization, and upserts."""

from __future__ import annotations

import asyncio
import html
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper, PaperVersion, Topic, utcnow

SEMANTIC_SCHOLAR_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_USER_AGENT = "XivPulse/0.1 (self-hosted research reader)"
_ARXIV_ID_RE = re.compile(
    r"(?P<id>(?:[a-z][a-z.\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)
_CATEGORY_RE = re.compile(r"^[a-zA-Z][a-zA-Z.\-]+$")


@dataclass(frozen=True, slots=True)
class NormalizedArxivId:
    base_id: str
    version: int = 1

    @property
    def versioned_id(self) -> str:
        return f"{self.base_id}v{self.version}"

    def __iter__(self):
        yield self.base_id
        yield self.version


@dataclass(slots=True)
class ArxivRecord:
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

    def json_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("published_at", "updated_at"):
            if value[key] is not None:
                value[key] = value[key].isoformat()
        return value


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


class _GlobalStartRateLimiter:
    """Reserve globally spaced request start times without holding an async lock."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = threading.Lock()
        self._next_start = 0.0

    async def wait(self, interval: float | None = None) -> None:
        spacing = self.interval if interval is None else max(0.0, interval)
        if spacing <= 0:
            return
        with self._lock:
            now = time.monotonic()
            reserved = max(now, self._next_start)
            self._next_start = reserved + spacing
        delay = reserved - now
        if delay > 0:
            await asyncio.sleep(delay)

    def reset(self) -> None:
        with self._lock:
            self._next_start = 0.0


_GLOBAL_LIMITER = _GlobalStartRateLimiter(3.0)


async def polite_wait(interval: float = 3.0) -> None:
    await _GLOBAL_LIMITER.wait(interval)


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


def normalize_arxiv_id(value: str) -> str:
    """Return a canonical identifier without a version suffix."""

    return parse_arxiv_id(value).base_id


def extract_arxiv_version(value: str) -> int:
    return parse_arxiv_id(value).version


def _clean_text(value: str | None) -> str:
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


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite's timezone-naive round trips before comparisons."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_html_affiliations(payload: str | bytes) -> list[str]:
    """Extract only explicitly marked affiliations from an arXiv HTML author block."""

    soup = BeautifulSoup(payload, "html.parser")
    values: list[str] = []
    for element in soup.select(".ltx_authors .ltx_role_affiliation"):
        for label in element.select(".ltx_contact_name"):
            label.decompose()
        value = _clean_text(element.get_text(" ", strip=True)).strip(" ?:;,.")
        if value and value.casefold() != "affiliation":
            values.append(value)
    return list(dict.fromkeys(values))


def deduplicate_records(records: Iterable[ArxivRecord]) -> list[ArxivRecord]:
    selected: dict[str, ArxivRecord] = {}
    positions: dict[str, int] = {}
    for position, record in enumerate(records):
        current = selected.get(record.arxiv_id)
        current_time = current.updated_at if current else None
        record_time = record.updated_at
        newer = current is None or record.version > current.version
        if current is not None and record.version == current.version:
            newer = (_as_utc(record_time) or datetime.min.replace(tzinfo=UTC)) > (
                _as_utc(current_time) or datetime.min.replace(tzinfo=UTC)
            )
        if newer:
            selected[record.arxiv_id] = record
            positions.setdefault(record.arxiv_id, position)
    return sorted(selected.values(), key=lambda item: positions[item.arxiv_id])


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return _clean_text(child.text)
    return None


def _parse_atom_entry(entry: ET.Element) -> ArxivRecord:
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
                if _local_name(item.tag) == "affiliation"
                and (value := _clean_text(item.text))
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
    record = ArxivRecord(
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
    record.canonicalize_urls()
    return record


def parse_arxiv_feed(payload: str | bytes) -> tuple[int, list[ArxivRecord]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("Invalid arXiv Atom response") from exc
    total_text = next(
        (
            _clean_text(element.text)
            for element in root.iter()
            if _local_name(element.tag) == "totalResults"
        ),
        "",
    )
    try:
        total = int(total_text)
    except ValueError as exc:
        raise ValueError("arXiv response is missing a valid totalResults") from exc
    records = deduplicate_records(
        _parse_atom_entry(element)
        for element in root
        if _local_name(element.tag) == "entry"
    )
    records.sort(
        key=lambda item: (
            _as_utc(item.published_at) or datetime.min.replace(tzinfo=UTC),
            item.arxiv_id,
            item.version,
        )
    )
    return total, records


def build_date_query(target_date: date, categories: Sequence[str]) -> str:
    compact = target_date.strftime("%Y%m%d")
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    return f"({category_query}) AND submittedDate:[{compact}0000 TO {compact}2359]"


def _topic_attr(topic: Topic | dict[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(topic, dict):
        if name in topic:
            return topic[name]
        return topic.get(f"{name}_json", default)
    return getattr(topic, name, getattr(topic, f"{name}_json", default))


def build_profile_query(topics: Sequence[Topic | dict[str, Any]]) -> str:
    clauses: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not _topic_attr(topic, "enabled", True):
            continue
        categories = _topic_attr(topic, "arxiv_categories", []) or []
        categories = [
            value for value in categories if isinstance(value, str) and _CATEGORY_RE.match(value)
        ]
        query = str(_topic_attr(topic, "query", "") or "").strip()
        keywords = _topic_attr(topic, "keywords", []) or []
        if query:
            interest = f"({query})"
        else:
            terms = [str(value).strip() for value in keywords if str(value).strip()]
            interest = " OR ".join(f'all:"{term.replace(chr(34), "")}"' for term in terms)
            interest = f"({interest})" if interest else ""
        category_clause = " OR ".join(f"cat:{value}" for value in categories)
        if category_clause and interest:
            clause = f"(({category_clause}) AND {interest})"
        elif category_clause:
            clause = f"({category_clause})"
        else:
            clause = interest
        key = clause.casefold()
        if clause and key not in seen:
            seen.add(key)
            clauses.append(clause)
    return " OR ".join(clauses) or "cat:cs.RO"


class ArxivClient:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        rate_limit_seconds: float = 3.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._client = http_client
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.timeout = timeout
        self.user_agent = user_agent

    async def _get(
        self,
        url: str,
        *,
        accept: str,
        request_timeout: float | None = None,
        params: dict[str, str | int] | None = None,
    ) -> bytes:
        await polite_wait(self.rate_limit_seconds)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
        }

        async def request() -> httpx.Response:
            if self._client is not None:
                return await self._client.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            async with httpx.AsyncClient(follow_redirects=True) as client:
                return await client.get(url, headers=headers, params=params, timeout=self.timeout)

        if request_timeout is None:
            response = await request()
        else:
            async with asyncio.timeout(max(1.0, request_timeout)):
                response = await request()
        response.raise_for_status()
        if len(response.content) > 20 * 1024 * 1024:
            raise ValueError("arXiv feed response exceeded 20 MiB")
        return response.content

    async def fetch_semantic_scholar_affiliations(
        self, arxiv_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        """Fetch publication-specific affiliations using exact arXiv identifiers."""

        clean = list(
            dict.fromkeys(
                normalized.base_id
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
                for normalized in [parse_arxiv_id(value)]
            )
        )[:100]
        if not clean:
            return {}
        params = {"fields": "externalIds,authors,authors.affiliations"}
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            response = await self._client.post(
                SEMANTIC_SCHOLAR_BATCH_URL,
                params=params,
                headers=headers,
                json={"ids": [f"ARXIV:{value}" for value in clean]},
                timeout=min(self.timeout, 20.0),
            )
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.post(
                    SEMANTIC_SCHOLAR_BATCH_URL,
                    params=params,
                    headers=headers,
                    json={"ids": [f"ARXIV:{value}" for value in clean]},
                    timeout=min(self.timeout, 20.0),
                )
        response.raise_for_status()
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Semantic Scholar response exceeded 10 MiB")
        payload = response.json()
        if not isinstance(payload, list):
            return {}
        expected = set(clean)
        result: dict[str, list[str]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            external_ids = item.get("externalIds")
            raw_id = external_ids.get("ArXiv") if isinstance(external_ids, dict) else None
            try:
                arxiv_id = parse_arxiv_id(str(raw_id)).base_id
            except ValueError:
                continue
            if arxiv_id not in expected:
                continue
            affiliations = list(
                dict.fromkeys(
                    _clean_text(value)
                    for author in item.get("authors", [])
                    if isinstance(author, dict)
                    for value in author.get("affiliations", [])
                    if isinstance(value, str) and _clean_text(value)
                )
            )
            if affiliations:
                result[arxiv_id] = affiliations
        return result

    async def fetch_html_affiliations(self, arxiv_ids: Sequence[str]) -> dict[str, list[str]]:
        """Fetch explicit author affiliations without inspecting paper body text."""

        clean = list(
            dict.fromkeys(
                normalized.base_id
                for value in arxiv_ids
                if isinstance(value, str) and value.strip()
                for normalized in [parse_arxiv_id(value)]
            )
        )

        async def fetch_one(arxiv_id: str) -> tuple[str, list[str]]:
            try:
                payload = await self._get(
                    f"https://arxiv.org/html/{quote(arxiv_id, safe='/.')}",
                    accept="text/html,application/xhtml+xml",
                    request_timeout=10.0,
                )
            except (TimeoutError, httpx.HTTPError, ValueError):
                return arxiv_id, []
            return arxiv_id, parse_html_affiliations(payload)

        # polite_wait serializes request starts globally at the configured
        # interval; concurrent tasks avoid adding slow response times in series.
        fetched = await asyncio.gather(*(fetch_one(arxiv_id) for arxiv_id in clean))
        return {arxiv_id: affiliations for arxiv_id, affiliations in fetched if affiliations}

    async def fetch_daily(
        self,
        topics: Sequence[Topic | dict[str, Any]],
        *,
        target_date: date,
        max_results: int = 100,
    ) -> list[ArxivRecord]:
        categories = [
            category
            for topic in topics
            if _topic_attr(topic, "enabled", True)
            for category in (_topic_attr(topic, "arxiv_categories", []) or [])
        ]
        clean = list(
            dict.fromkeys(
                value.strip()
                for value in categories
                if isinstance(value, str) and _CATEGORY_RE.match(value.strip())
            )
        )
        if not clean:
            raise ValueError("At least one valid arXiv category is required")
        limit = min(max(1, int(max_results)), 2000)
        payload = await self._get(
            ARXIV_API_URL,
            accept="application/atom+xml,application/xml",
            params={
                "search_query": build_date_query(target_date, clean),
                "start": 0,
                "max_results": limit,
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
            },
        )
        total, records = parse_arxiv_feed(payload)
        if total > limit:
            raise ValueError(
                f"arXiv reports {total} papers for {target_date}, exceeding the configured "
                f"limit {limit}"
            )
        outside_date = [
            record.arxiv_id
            for record in records
            if record.published_at is None or record.published_at.date() != target_date
        ]
        if outside_date:
            raise ValueError(
                f"arXiv returned papers outside requested date {target_date}: "
                + ", ".join(outside_date[:3])
            )
        return records


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
        record_updated = _as_utc(record.updated_at)
        stored_updated = _as_utc(paper.arxiv_updated_at)
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


fetch_daily_papers = ArxivClient.fetch_daily


__all__ = [
    "ARXIV_API_URL",
    "ArxivClient",
    "ArxivRecord",
    "NormalizedArxivId",
    "UpsertResult",
    "build_date_query",
    "build_profile_query",
    "deduplicate_records",
    "extract_arxiv_version",
    "normalize_arxiv_id",
    "parse_arxiv_feed",
    "parse_html_affiliations",
    "parse_arxiv_id",
    "polite_wait",
    "upsert_papers",
]
