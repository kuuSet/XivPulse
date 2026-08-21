"""Validated request, response, and model-output contracts."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TopicBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list)
    query: str = Field(default="", max_length=2000)
    arxiv_categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    context_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.0, le=5.0)
    enabled: bool = True

    @field_validator("name", "description", "query")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "aliases",
        "arxiv_categories",
        "keywords",
        "context_keywords",
        "negative_keywords",
    )
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class TopicWrite(TopicBase):
    id: int | None = None


class InterestsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[TopicWrite]
    watched_authors: list[str] = Field(default_factory=list)
    watched_organizations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_topic_directions(self) -> InterestsUpdate:
        if not any(topic.enabled for topic in self.topics):
            raise ValueError("至少需要启用一个研究方向")
        identities: dict[str, str] = {}
        for topic in self.topics:
            for raw in (topic.name, *topic.aliases):
                normalized = unicodedata.normalize("NFKC", raw).strip().casefold()
                key = re.sub(r"[\s_-]+", "", normalized)
                previous = identities.get(key)
                if previous is not None:
                    raise ValueError(
                        f"研究方向名称或别名不能重复：{raw} 与 {previous} 表示同一个方向；"
                        "请保留一个正式名称，并把其他写法放在该方向的别名中"
                    )
                identities[key] = raw
        return self


class PrefilterDecision(BaseModel):
    paper_id: int
    decision: Literal["relevant", "uncertain", "irrelevant"]


class PrefilterResult(BaseModel):
    items: list[PrefilterDecision]


class ScreeningResult(BaseModel):
    score: int = Field(ge=0, le=100)


class DeepAnalysisResult(BaseModel):
    summary: str
    problem: str
    key_contributions: list[str]
    methods: list[str]
    evidence: list[str]
    strengths: list[str]
    weaknesses: list[str]
    limitations: list[str]
    relevance_score: float = Field(ge=0, le=100)
    novelty_score: float = Field(ge=0, le=100)
    rigor_score: float = Field(ge=0, le=100)
    impact_score: float = Field(ge=0, le=100)
    recommendation: Literal["must_read", "read", "skim", "skip"]
    verdict: str
    follow_up_questions: list[str]
    tags: list[str]


class ProviderTestResult(BaseModel):
    ok: bool
    provider_name: str
    model: str
    transport: str
    message: str


__all__ = [
    "DeepAnalysisResult",
    "PrefilterDecision",
    "PrefilterResult",
    "InterestsUpdate",
    "ProviderTestResult",
    "ScreeningResult",
    "TopicWrite",
]
