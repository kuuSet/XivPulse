from datetime import UTC, date, datetime

from app.arxiv import command
from app.domain.paper import Paper


def test_fetch_arxiv_cli_outputs_jsonl(monkeypatch, capsys) -> None:
    received: dict[str, object] = {}
    paper = Paper(
        arxiv_id="2608.00001",
        version=1,
        title="A robot paper",
        categories=["cs.RO"],
        published_at=datetime(2026, 8, 13, 1, tzinfo=UTC),
    )

    class FakeArxivClient:
        async def fetch_daily(
            self,
            categories: list[str],
            *,
            target_date: date,
            max_results: int,
        ) -> list[Paper]:
            received.update(
                categories=categories,
                target_date=target_date,
                max_results=max_results,
            )
            return [paper]

    monkeypatch.setattr(command, "ArxivClient", FakeArxivClient)

    exit_code = command.main(
        [
            "--date",
            "2026-08-13",
            "--category",
            "cs.RO",
            "--category",
            "cs.AI",
            "--limit",
            "17",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert received == {
        "categories": ["cs.RO", "cs.AI"],
        "target_date": date(2026, 8, 13),
        "max_results": 17,
    }
    assert '"arxiv_id": "2608.00001"' in captured.out
    assert captured.out.count("\n") == 1
    assert captured.err == "Fetched 1 papers for 2026-08-13\n"
