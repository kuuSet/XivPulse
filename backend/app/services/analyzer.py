"""Structured paper screening and analysis over OpenAI-compatible APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from ..models import Paper, Topic
from ..schemas import DeepAnalysisResult, PrefilterResult, ScreeningResult
from .directions.matching import terms
from .fulltext import FullTextDocument, FullTextFetcher
from .prompt_catalog import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    DEFAULT_PROMPT_TEMPLATE_ID,
    PREFILTER_SYSTEM_PROMPT,
    SCREENING_SYSTEM_PROMPT,
    analysis_prompt_metadata,
    prompt_template,
)
from .provider import GenerationResult, StructuredModelAdapter


@dataclass(frozen=True, slots=True)
class AnalysisBudget:
    mode: Literal["economy", "balanced", "deep"]
    label: str
    max_fulltext_chars: int
    max_pdf_pages: int
    prefilter_output_tokens: int
    deep_output_tokens: int


ANALYSIS_BUDGETS: dict[str, AnalysisBudget] = {
    "economy": AnalysisBudget("economy", "经济", 16_000, 12, 900, 2_000),
    "balanced": AnalysisBudget("balanced", "平衡", 30_000, 25, 1_200, 3_500),
    "deep": AnalysisBudget("deep", "深度", 45_000, 40, 1_600, 5_000),
}


def analysis_budget(mode: str | None) -> AnalysisBudget:
    return ANALYSIS_BUDGETS.get(str(mode or "balanced"), ANALYSIS_BUDGETS["balanced"])


def _topic_context(topics: Sequence[Topic | Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for topic in topics:
        enabled = topic.get("enabled", True) if isinstance(topic, Mapping) else topic.enabled
        if not enabled:
            continue
        name = str(topic.get("name", "") if isinstance(topic, Mapping) else topic.name)
        raw_description = (
            topic.get("description", "") if isinstance(topic, Mapping) else topic.description
        )
        description = str(raw_description or "").strip()
        aliases = terms(topic, "aliases")
        alias_text = ", ".join(aliases[:12]) or "无"
        lines.append(f"- {name}；范围：{description or '未补充'}；可接受别名：{alias_text}")
    return "\n".join(lines) or "- 未配置关注标签"


def _paper_value(paper: Paper | Mapping[str, Any], name: str, default: Any = "") -> Any:
    if isinstance(paper, Mapping):
        if name in paper:
            return paper[name]
        return paper.get(f"{name}_json", default)
    return getattr(paper, name, getattr(paper, f"{name}_json", default))


def _xml_text(value: object) -> str:
    return escape(str(value), quote=False)


class PaperAnalyzer:
    def __init__(
        self,
        prefilter_adapter: StructuredModelAdapter,
        screening_adapter: StructuredModelAdapter,
        deep_adapter: StructuredModelAdapter,
        *,
        fulltext_fetcher: FullTextFetcher | None = None,
        budget_mode: str = "balanced",
        prompt_template_id: str = DEFAULT_PROMPT_TEMPLATE_ID,
        deep_analysis_prompt: str | None = None,
    ) -> None:
        self.prefilter_adapter = prefilter_adapter
        self.screening_adapter = screening_adapter
        self.deep_adapter = deep_adapter
        self.budget = analysis_budget(budget_mode)
        selected_prompt = prompt_template(prompt_template_id)
        self.prompt_template_id = selected_prompt.id
        self.deep_analysis_prompt = str(
            deep_analysis_prompt or selected_prompt.deep_analysis_prompt
        ).strip()
        self.fulltext_fetcher = fulltext_fetcher or FullTextFetcher(
            max_chars=self.budget.max_fulltext_chars,
            max_pdf_pages=self.budget.max_pdf_pages,
        )

    async def prefilter_with_metadata(
        self,
        papers: Sequence[Paper | Mapping[str, Any]],
        topics: Sequence[Topic | Mapping[str, Any]],
    ) -> GenerationResult[PrefilterResult]:
        paper_blocks = []
        for paper in papers:
            paper_blocks.append(
                f'<paper id="{_paper_value(paper, "id")}">\n'
                f"标题：{_xml_text(_paper_value(paper, 'title'))}\n"
                f"分类：{_xml_text(', '.join(_paper_value(paper, 'categories', []) or []))}\n"
                "</paper>"
            )
        prompt = (
            f"<followed_topics>\n{_xml_text(_topic_context(topics))}\n</followed_topics>\n\n"
            f"<papers>\n{'\n'.join(paper_blocks)}\n</papers>"
        )
        generated = await self.prefilter_adapter.generate_json_with_metadata(
            system_prompt=PREFILTER_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=PrefilterResult,
            schema_name="paper_title_prefilter",
            max_output_tokens=self.budget.prefilter_output_tokens,
        )
        generated.prompt_metadata = self.prompt_metadata("prefilter")
        return generated

    async def screen_with_metadata(
        self,
        paper: Paper | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]],
    ) -> GenerationResult[ScreeningResult]:
        topics = list(topics)
        prompt = (
            f"<followed_topics>\n关注标签：\n{_xml_text(_topic_context(topics))}\n"
            f"</followed_topics>\n\n"
            f"<paper_data>\n论文题目：{_xml_text(_paper_value(paper, 'title'))}\n"
            f"分类：{_xml_text(', '.join(_paper_value(paper, 'categories', []) or []))}\n"
            f"摘要：{_xml_text(_paper_value(paper, 'abstract'))}\n</paper_data>"
        )
        generated = await self.screening_adapter.generate_json_with_metadata(
            system_prompt=SCREENING_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=ScreeningResult,
            schema_name="paper_screening",
            max_output_tokens=64,
        )
        generated.prompt_metadata = self.prompt_metadata("screening")
        return generated

    async def deep_analyze_with_metadata(
        self,
        paper: Paper | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]],
        *,
        include_fulltext: bool = True,
    ) -> tuple[GenerationResult[DeepAnalysisResult], FullTextDocument]:
        topics = list(topics)
        fulltext = FullTextDocument("", "unavailable", None)
        if include_fulltext:
            if isinstance(paper, Paper):
                fulltext_target: Paper | str | None = paper
            else:
                raw_id = _paper_value(paper, "arxiv_id", None)
                fulltext_target = str(raw_id) if raw_id else None
            if fulltext_target is not None:
                fulltext = await self.fulltext_fetcher.fetch(fulltext_target)
        source_label = fulltext.source if fulltext.content else "abstract"
        source_text = fulltext.content or str(_paper_value(paper, "abstract"))
        prompt = (
            f"<analysis_preferences>\n{_xml_text(self.deep_analysis_prompt)}\n"
            f"</analysis_preferences>\n\n"
            f"<followed_topics>\n关注标签：\n{_xml_text(_topic_context(topics))}\n"
            f"</followed_topics>\n\n"
            f"<paper_data>\n论文题目：{_xml_text(_paper_value(paper, 'title'))}\n"
            f"作者：{_xml_text(', '.join(_paper_value(paper, 'authors', []) or []))}\n"
            f"arXiv 分类："
            f"{_xml_text(', '.join(_paper_value(paper, 'categories', []) or []))}\n"
            f"分析文本来源：{source_label}{'（已截断）' if fulltext.truncated else ''}\n\n"
            f"论文文本：\n{_xml_text(source_text)}\n</paper_data>"
        )
        result = await self.deep_adapter.generate_json_with_metadata(
            system_prompt=DEEP_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=DeepAnalysisResult,
            schema_name="deep_paper_analysis",
            max_output_tokens=self.budget.deep_output_tokens,
        )
        result.prompt_metadata = self.prompt_metadata("deep")
        return result, fulltext

    def prompt_metadata(self, stage: Literal["prefilter", "screening", "deep"]) -> dict[str, Any]:
        return analysis_prompt_metadata(
            self.prompt_template_id,
            self.deep_analysis_prompt,
            stage,
        )


__all__ = [
    "ANALYSIS_BUDGETS",
    "AnalysisBudget",
    "analysis_budget",
    "PaperAnalyzer",
]
