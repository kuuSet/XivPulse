"""Structured paper screening and analysis over OpenAI-compatible APIs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..models import Analysis, Paper, Profile, Topic
from ..schemas import DeepAnalysisResult, ScreeningResult
from .direction_matching import (
    canonical_requested_direction_names,
    direction_evidence,
    terms,
)
from .direction_taxonomy import stamp_direction_attribution
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


def _has_configured_term(text: str, term: object) -> bool:
    phrase = str(term).strip()
    if not phrase:
        return False
    if re.fullmatch(r"[A-Za-z0-9_+.-]+", phrase):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.I) is not None
    return phrase.casefold() in text.casefold()


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
        deep_prompt: str | None = None,
    ) -> None:
        self.adapter = adapter or OpenAICompatibleAdapter()
        self.budget = analysis_budget(budget_mode)
        selected_prompt = prompt_template(prompt_template_id)
        self.prompt_template_id = selected_prompt.id
        self.screening_prompt = str(screening_prompt or selected_prompt.screening_prompt).strip()
        self.deep_prompt = str(deep_prompt or selected_prompt.deep_prompt).strip()
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
            mock_factory=lambda: self._mock_screen(paper, topics),
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
        # Mock mode is deliberately offline-friendly. The abstract still gives a
        # complete demo response, while configured providers use HTML/PDF evidence.
        if include_fulltext and not self.adapter.mock_mode:
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
            f"<analysis_preferences>\n{_xml_text(self.deep_prompt)}\n"
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
            mock_factory=lambda: self._mock_deep(paper, topics),
        )
        result.prompt_metadata = self.prompt_metadata("deep")
        return result, fulltext

    def prompt_metadata(self, stage: Literal["screening", "deep"]) -> dict[str, Any]:
        return analysis_prompt_metadata(
            self.prompt_template_id,
            self.screening_prompt,
            self.deep_prompt,
            stage,
        )

    async def screen_and_store(
        self,
        session: Session,
        paper: Paper,
        profile: Profile,
        topics: Sequence[Topic] | None = None,
    ) -> Analysis:
        active_topics = list(topics if topics is not None else profile.topics)
        generated = await self.screen_with_metadata(paper, profile, active_topics)
        data = generated.data
        payload = stamp_direction_attribution(data.model_dump(), active_topics)
        payload["_analysis_prompt"] = generated.prompt_metadata
        analysis = Analysis(
            paper_id=paper.id,
            profile_id=profile.id,
            kind="screening",
            status="completed",
            provider=generated.provider,
            model=generated.model,
            transport=generated.transport,
            content_source="abstract",
            summary=data.reason,
            verdict="relevant" if data.relevant else "not_relevant",
            relevance_score=data.relevance_score,
            tags_json=data.tags,
            payload_json=payload,
            **generated.usage.analysis_fields(self.budget.mode),
        )
        session.add(analysis)
        session.flush()
        return analysis

    async def deep_analyze_and_store(
        self,
        session: Session,
        paper: Paper,
        profile: Profile,
        topics: Sequence[Topic] | None = None,
        *,
        include_fulltext: bool = True,
    ) -> Analysis:
        generated, fulltext = await self.deep_analyze_with_metadata(
            paper, profile, topics, include_fulltext=include_fulltext
        )
        data = generated.data
        payload = data.model_dump()
        payload["_analysis_prompt"] = generated.prompt_metadata
        analysis = Analysis(
            paper_id=paper.id,
            profile_id=profile.id,
            kind="deep",
            status="completed",
            provider=generated.provider,
            model=generated.model,
            transport=generated.transport,
            content_source=fulltext.source if fulltext.content else "abstract",
            summary=data.summary,
            verdict=data.verdict,
            relevance_score=data.relevance_score,
            novelty_score=data.novelty_score,
            rigor_score=data.rigor_score,
            impact_score=data.impact_score,
            strengths_json=data.strengths,
            weaknesses_json=data.weaknesses,
            tags_json=data.tags,
            payload_json=payload,
            **generated.usage.analysis_fields(self.budget.mode),
        )
        session.add(analysis)
        session.flush()
        return analysis

    @staticmethod
    def _mock_screen(
        paper: Paper | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]],
    ) -> ScreeningResult:
        title = str(_paper_value(paper, "title"))
        abstract = str(_paper_value(paper, "abstract"))
        text = f"{title}\n{abstract}"
        matched_topics: list[str] = []
        matched_keywords: list[str] = []
        excluded: list[str] = []
        for topic in topics:
            if isinstance(topic, Mapping):
                if not topic.get("enabled", True):
                    continue
                name = str(topic.get("name", "未命名主题"))
            else:
                if not topic.enabled:
                    continue
                name = topic.name
            evidence = direction_evidence(text, topic)
            if evidence.matched:
                matched_topics.append(name)
                matched_keywords.extend(evidence.keywords)
            if evidence.keywords:
                excluded.extend(evidence.exclusions)
        score = min(95.0, 12.0 + 18.0 * len(set(matched_topics)) + 8.0 * len(set(matched_keywords)))
        score = max(0.0, score - 20.0 * len(set(excluded)))
        relevant = score >= 35.0
        first_sentence = (
            re.split(r"(?<=[.!?。！？])\s+", abstract.strip())[0] if abstract.strip() else title
        )
        contributions = [first_sentence[:300]] if first_sentence else ["摘要未提供可核查的贡献描述"]
        reason = (
            f"本地演示初筛命中 {len(set(matched_topics))} 个研究方向、"
            f"{len(set(matched_keywords))} 个关键词。"
        )
        if excluded:
            reason += f" 同时命中排除词：{'、'.join(dict.fromkeys(excluded))}。"
        return ScreeningResult(
            relevant=relevant,
            relevance_score=round(score, 2),
            reason=reason,
            matched_topics=list(dict.fromkeys(matched_topics)),
            key_contributions=contributions,
            tags=list(dict.fromkeys(matched_keywords))[:8],
        )

    def _mock_deep(
        self,
        paper: Paper | Mapping[str, Any],
        topics: Sequence[Topic | Mapping[str, Any]],
    ) -> DeepAnalysisResult:
        screening = self._mock_screen(paper, topics)
        title = str(_paper_value(paper, "title"))
        abstract = str(_paper_value(paper, "abstract"))
        summary = abstract.strip()[:700] or f"{title}（摘要暂缺）"
        base = screening.relevance_score
        recommendation: Literal["must_read", "read", "skim", "skip"]
        if base >= 75:
            recommendation = "read"
        elif base >= 45:
            recommendation = "skim"
        else:
            recommendation = "skip"
        return DeepAnalysisResult(
            summary=summary,
            problem=f"论文围绕“{title}”所描述的问题展开；当前结论仅基于 arXiv 元数据与摘要。",
            key_contributions=screening.key_contributions,
            methods=["演示模式未读取全文，方法细节需配置 API 后核验"],
            evidence=["当前仅有摘要证据，未核验实验表格、消融或附录"],
            strengths=["研究方向与研究画像存在可解释的关键词匹配"] if screening.relevant else [],
            weaknesses=["演示模式不能形成可靠的学术质量判断"],
            limitations=["未调用外部模型", "未基于全文核验作者主张"],
            relevance_score=base,
            novelty_score=50.0,
            rigor_score=45.0,
            impact_score=50.0,
            recommendation=recommendation,
            verdict="这是可运行的本地演示评价；配置模型 API 后再据全文形成正式判断。",
            follow_up_questions=["论文是否在真实机器人上验证？", "是否提供代码、数据与关键消融？"],
            tags=screening.tags,
        )


__all__ = [
    "ANALYSIS_BUDGETS",
    "AnalysisBudget",
    "analysis_budget",
    "PaperAnalyzer",
]
