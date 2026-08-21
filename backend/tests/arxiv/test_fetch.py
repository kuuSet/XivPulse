"""arXiv daily-fetch tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import arxiv
import pytest
from app.arxiv.fetch import ArxivFetchError, fetch_daily
from app.domain.paper import Paper as PaperDocument


class _FakeArxivMetadataClient:
    def __init__(
        self,
        results: list[arxiv.Result] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = results or []
        self.error = error
        self.searches: list[arxiv.Search] = []

    def results(self, search: arxiv.Search):
        self.searches.append(search)
        if self.error is not None:
            raise self.error
        return iter(self.items)


def _arxiv_result(
    arxiv_id: str,
    *,
    version: int = 1,
    title: str = "Test paper",
    published: datetime = datetime(2026, 8, 13, 1, tzinfo=UTC),
    updated: datetime = datetime(2026, 8, 13, 2, tzinfo=UTC),
) -> arxiv.Result:
    versioned = f"{arxiv_id}v{version}"
    return arxiv.Result(
        entry_id=f"https://arxiv.org/abs/{versioned}",
        updated=updated,
        published=published,
        title=title,
        authors=[arxiv.Result.Author("Test Author", affiliation=["OpenAI"])],
        summary="Test abstract.",
        comment="12 pages, 3 figures",
        journal_ref="Journal of Robots 1 (2026)",
        doi="10.1000/robot.1",
        primary_category="cs.RO",
        categories=["cs.RO", "cs.AI"],
        links=[
            arxiv.Result.Link(
                f"https://arxiv.org/pdf/{versioned}",
                title="pdf",
                rel="related",
                content_type="application/pdf",
            )
        ],
    )


@pytest.mark.asyncio
async def test_daily_fetch_uses_arxiv_library_query_and_maps_results() -> None:
    metadata = _FakeArxivMetadataClient([_arxiv_result("2608.00001", version=2)])
    records = await fetch_daily(
        [" cs.RO ", "cs.AI", "cs.RO"],
        target_date=date(2026, 8, 13),
        limit=17,
        client=metadata,
        rate_limit_seconds=0,
    )

    search = metadata.searches[0]
    assert search.query == (
        "(cat:cs.RO OR cat:cs.AI) AND submittedDate:[202608130000 TO 202608132359]"
    )
    assert search.max_results == 18
    assert search.sort_by is arxiv.SortCriterion.SubmittedDate
    assert search.sort_order is arxiv.SortOrder.Ascending
    assert len(records) == 1
    assert isinstance(records[0], PaperDocument)
    assert records[0].arxiv_id == "2608.00001"
    assert records[0].version == 2
    assert records[0].affiliations == ["OpenAI"]
    assert records[0].comment == "12 pages, 3 figures"
    assert records[0].journal_ref == "Journal of Robots 1 (2026)"
    assert records[0].doi == "10.1000/robot.1"
    assert records[0].published_at == datetime(2026, 8, 13, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_daily_fetch_configures_paging_delay_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options: dict[str, object] = {}

    class FakeClient(_FakeArxivMetadataClient):
        def __init__(self, **values: object) -> None:
            super().__init__()
            options.update(values)

    monkeypatch.setattr(arxiv, "Client", FakeClient)
    await fetch_daily(
        ["cs.RO"],
        target_date=date(2026, 8, 13),
        limit=1,
        rate_limit_seconds=1.5,
    )

    assert options == {"page_size": 200, "delay_seconds": 1.5, "num_retries": 2}


@pytest.mark.asyncio
async def test_daily_fetch_wraps_metadata_client_errors() -> None:
    error = RuntimeError("network failed")
    metadata = _FakeArxivMetadataClient(error=error)

    with pytest.raises(ArxivFetchError, match="metadata request failed") as raised:
        await fetch_daily(
            ["cs.RO"],
            target_date=date(2026, 8, 13),
            limit=17,
            client=metadata,
        )

    assert raised.value.__cause__ is error


@pytest.mark.asyncio
async def test_daily_fetch_deduplicates_library_results() -> None:
    metadata = _FakeArxivMetadataClient(
        [
            _arxiv_result("2608.00200", title="Paper 200"),
            _arxiv_result("2608.00200", version=2, title="Paper 200 revised"),
            _arxiv_result("2608.00201", title="Paper 201"),
        ]
    )

    records = await fetch_daily(
        ["cs.RO"],
        target_date=date(2026, 8, 13),
        limit=3,
        client=metadata,
    )

    assert len(records) == 2
    assert records[0].version == 2
    assert records[0].title == "Paper 200 revised"


@pytest.mark.asyncio
async def test_daily_fetch_rejects_papers_outside_requested_date() -> None:
    metadata = _FakeArxivMetadataClient(
        [_arxiv_result("2608.00001", published=datetime(2026, 8, 12, 1, tzinfo=UTC))]
    )

    with pytest.raises(ArxivFetchError, match="outside requested date 2026-08-13"):
        await fetch_daily(
            ["cs.RO"],
            target_date=date(2026, 8, 13),
            limit=1,
            client=metadata,
        )


@pytest.mark.asyncio
async def test_daily_fetch_rejects_results_above_limit() -> None:
    metadata = _FakeArxivMetadataClient(
        [_arxiv_result(f"2608.{index:05d}") for index in range(1, 4)]
    )

    with pytest.raises(ArxivFetchError, match="configured limit 2"):
        await fetch_daily(
            ["cs.RO"],
            target_date=date(2026, 8, 13),
            limit=2,
            client=metadata,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("categories", "limit", "message"),
    [
        ([], 10, "valid arXiv category"),
        (["cs.RO", "not a category"], 10, "Invalid arXiv categories"),
        (["cs.RO"], 0, "result limit must be positive"),
    ],
)
async def test_daily_fetch_keeps_invalid_input_separate_from_fetch_errors(
    categories: list[str],
    limit: int,
    message: str,
) -> None:
    metadata = _FakeArxivMetadataClient()
    with pytest.raises(ValueError, match=message):
        await fetch_daily(
            categories,
            target_date=date(2026, 8, 13),
            limit=limit,
            client=metadata,
        )
    assert metadata.searches == []
