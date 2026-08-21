from __future__ import annotations

from typing import Any

import pytest
from app.models import Paper, Topic
from app.schemas import PrefilterDecision, PrefilterResult
from app.services.analysis.analyzer import PaperAnalyzer
from app.services.provider import GenerationResult


@pytest.mark.asyncio
async def test_title_prefilter_batches_papers_without_sending_abstracts() -> None:
    captured: dict[str, Any] = {}

    class FakeAdapter:
        async def generate_json_with_metadata(self, **values: Any) -> GenerationResult:
            captured.update(values)
            return GenerationResult(
                data=PrefilterResult(
                    items=[
                        PrefilterDecision(paper_id=1, decision="relevant"),
                        PrefilterDecision(paper_id=2, decision="uncertain"),
                        PrefilterDecision(paper_id=3, decision="irrelevant"),
                    ]
                ),
                transport="responses",
                provider="test",
                model="test",
            )

    adapter = FakeAdapter()
    analyzer = PaperAnalyzer(adapter, adapter, adapter)  # type: ignore[arg-type]
    result = await analyzer.prefilter_with_metadata(
        [
            Paper(
                id=1,
                arxiv_id="2608.10001",
                title="Distributed scheduling for language-model serving",
                abstract="SECRET_ABSTRACT_ONE",
                categories_json=["cs.DC", "cs.LG"],
            ),
            Paper(
                id=2,
                arxiv_id="2608.10002",
                title="An ambiguous systems paper",
                abstract="SECRET_ABSTRACT_TWO",
                categories_json=["cs.AI"],
            ),
            Paper(
                id=3,
                arxiv_id="2608.10003",
                title="Algebraic topology of compact manifolds",
                abstract="SECRET_ABSTRACT_THREE",
                categories_json=["math.AT"],
            ),
        ],
        [
            Topic(
                name="LLM Systems",
                description="Efficient inference, serving, scheduling, and memory management.",
                enabled=True,
            )
        ],
    )

    prompt = captured["user_prompt"]
    assert "Distributed scheduling for language-model serving" in prompt
    assert "cs.DC, cs.LG" in prompt
    assert "Efficient inference, serving" in prompt
    assert "SECRET_ABSTRACT" not in prompt
    assert [item.decision for item in result.data.items] == [
        "relevant",
        "uncertain",
        "irrelevant",
    ]
