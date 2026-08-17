"""Structured paper screening and analysis over OpenAI-compatible APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from ..models import Paper, Profile, Topic
from ..schemas import DeepAnalysisResult, ScreeningResult
from .directions.matching import (
    canonical_requested_direction_names,
    terms,
)
from .fulltext import FullTextDocument, FullTextFetcher
from .prompt_catalog import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    DEFAULT_PROMPT_TEMPLATE_ID,
    SCREENING_SYSTEM_PROMPT,
    analysis_prompt_metadata,
    prompt_template,
)
from .provider import GenerationResult, OpenAICompatibleAdapter


@dataclass(frozen=True, slots=True)
class AnalysisBudget:
    mode: Literal["economy", "balanced", "deep"]
    label: str
    max_fulltext_chars: int
    max_pdf_pages: int
    screening_output_tokens: int
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
        aliases = terms(topic, "aliases")
        keywords = (
            topic.get("keywords", topic.get("keywords_json", []))
            if isinstance(topic, Mapping)
            else topic.keywords
        )
        negatives = (
            topic.get("negative_keywords", topic.get("negative_keywords_json", []))
            if isinstance(topic, Mapping)
            else topic.negative_keywords
        )
        anchors = terms(topic, "context_keywords")
        positive_text = ", ".join(str(value) for value in keywords[:12]) or "无"
        alias_text = ", ".join(aliases[:12]) or "无"
        anchor_text = ", ".join(anchors[:12]) or "无需额外领域锚点"
        negative_text = ", ".join(str(value) for value in negatives[:12]) or "无"
        lines.append(
            f"- {name}；可接受别名：{alias_text}；方向词：{positive_text}；"
            f"领域锚点：{anchor_text}；排除词：{negative_text}"
        )
    return "\n".join(lines) or "- 具身智能与机器人学习"


def _paper_value(paper: Paper | Mapping[str, Any], name: str, default: Any = "") -> Any:
    if isinstance(paper, Mapping):
        if name in paper:
            return paper[name]
        return paper.get(f"{name}_json", default)
    return getattr(paper, name, getattr(paper, f"{name}_json", default))


def _profile_value(profile: Profile | Mapping[str, Any], name: str, default: Any = "") -> Any:
    if isinstance(profile, Mapping):
        return profile.get(name, default)
    return getattr(profile, name, default)


def _xml_text(value: object) -> str:
    return escape(str(value), quote=False)


class PaperAnalyzer:
    def __init__(
        self,
        adapter: OpenAICompatibleAdapter | None = None,
        *,
        fulltext_fetcher: FullTextFetcher | None = None,
        budget_mode: str = "balanced",
        prompt_template_id: str = DEFAULT_PROMPT_TEMPLATE_ID,
        screening_prompt: str | None = None,
        deep_analysis_prompt: str | None = None,
    ) -> None:
        self.adapter = adapter or OpenAICompatibleAdapter()
        self.budget = analysis_budget(budget_mode)
        selected_prompt = prompt_template(prompt_template_id)
        self.prompt_template_id = selected_prompt.id
        self.screening_prompt = str(screening_prompt or selected_prompt.screening_prompt).strip()
        self.deep_analysis_prompt = str(
            deep_analysis_prompt or selected_prompt.deep_analysis_prompt
        ).strip()
        self.fulltext_fetcher = fulltext_fetcher or FullTextFetcher(
            max_chars=self.budget.max_fulltext_chars,
            max_pdf_pages=self.budget.max_pdf_pages,
        )

    async def screen(
        self,
        paper: Paper | Mapping[str, Any],
        profile: Profile | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]] | None = None,
    ) -> ScreeningResult:
        return (await self.screen_with_metadata(paper, profile, topics)).data

    async def screen_with_metadata(
        self,
        paper: Paper | Mapping[str, Any],
        profile: Profile | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]] | None = None,
    ) -> GenerationResult[ScreeningResult]:
        topics = list(topics if topics is not None else _profile_value(profile, "topics", []))
        prompt = (
            f"<analysis_preferences>\n{_xml_text(self.screening_prompt)}\n"
            f"</analysis_preferences>\n\n"
            f"<research_profile>\n研究画像："
            f"{_xml_text(_profile_value(profile, 'description'))}\n"
            f"正式研究方向及归因关键词：\n{_xml_text(_topic_context(topics))}\n"
            f"</research_profile>\n\n"
            f"<paper_data>\n论文题目：{_xml_text(_paper_value(paper, 'title'))}\n"
            f"分类：{_xml_text(', '.join(_paper_value(paper, 'categories', []) or []))}\n"
            f"摘要：{_xml_text(_paper_value(paper, 'abstract'))}\n</paper_data>"
        )
        generated = await self.adapter.generate_json_with_metadata(
            system_prompt=SCREENING_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=ScreeningResult,
            schema_name="paper_screening",
            max_output_tokens=self.budget.screening_output_tokens,
        )
        generated.prompt_metadata = self.prompt_metadata("screening")
        verified_topics = canonical_requested_direction_names(
            generated.data.matched_topics,
            topics,
            relevant=generated.data.relevant,
        )
        generated.data = generated.data.model_copy(update={"matched_topics": verified_topics})
        return generated

    async def deep_analyze(
        self,
        paper: Paper | Mapping[str, Any],
        profile: Profile | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]] | None = None,
        *,
        include_fulltext: bool = True,
    ) -> DeepAnalysisResult:
        result, _ = await self.deep_analyze_with_metadata(
            paper, profile, topics, include_fulltext=include_fulltext
        )
        return result.data

    async def deep_analyze_with_metadata(
        self,
        paper: Paper | Mapping[str, Any],
        profile: Profile | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]] | None = None,
        *,
        include_fulltext: bool = True,
    ) -> tuple[GenerationResult[DeepAnalysisResult], FullTextDocument]:
        topics = list(topics if topics is not None else _profile_value(profile, "topics", []))
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
            f"<research_profile>\n研究画像："
            f"{_xml_text(_profile_value(profile, 'description'))}\n"
            f"正式研究方向及归因关键词：\n{_xml_text(_topic_context(topics))}\n"
            f"</research_profile>\n\n"
            f"<paper_data>\n论文题目：{_xml_text(_paper_value(paper, 'title'))}\n"
            f"作者：{_xml_text(', '.join(_paper_value(paper, 'authors', []) or []))}\n"
            f"arXiv 分类："
            f"{_xml_text(', '.join(_paper_value(paper, 'categories', []) or []))}\n"
            f"分析文本来源：{source_label}{'（已截断）' if fulltext.truncated else ''}\n\n"
            f"论文文本：\n{_xml_text(source_text)}\n</paper_data>"
        )
        result = await self.adapter.generate_json_with_metadata(
            system_prompt=DEEP_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=DeepAnalysisResult,
            schema_name="deep_paper_analysis",
            max_output_tokens=self.budget.deep_output_tokens,
        )
        result.prompt_metadata = self.prompt_metadata("deep")
        return result, fulltext

    def prompt_metadata(self, stage: Literal["screening", "deep"]) -> dict[str, Any]:
        return analysis_prompt_metadata(
            self.prompt_template_id,
            self.screening_prompt,
            self.deep_analysis_prompt,
            stage,
        )

__all__ = [
    "ANALYSIS_BUDGETS",
    "AnalysisBudget",
    "analysis_budget",
    "PaperAnalyzer",
]
