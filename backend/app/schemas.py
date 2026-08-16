"""Validated request, response, and model-output contracts."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="ignore")


class TopicBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list)
    query: str = Field(default="", max_length=2000)
    arxiv_categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    context_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.0, le=5.0)
    enabled: bool = True

    @field_validator("name", "query")
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


class TopicRead(TopicBase, ORMModel):
    id: int
    profile_id: int
    created_at: datetime
    updated_at: datetime


class ProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10000)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    active: bool = True


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10000)
    language: str | None = Field(default=None, min_length=2, max_length=20)
    active: bool | None = None
    topics: list[TopicWrite] | None = None
    author_watchlist: list[str] | None = None

    @model_validator(mode="after")
    def validate_topic_directions(self) -> ProfileUpdate:
        if self.topics is None:
            return self
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


class ProfileRead(ProfileBase, ORMModel):
    id: int
    topics: list[TopicRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PaperVersionRead(ORMModel):
    id: int
    version: int
    arxiv_version_id: str
    title: str
    abstract: str
    authors: list[str]
    affiliations: list[str]
    categories: list[str]
    abs_url: str
    html_url: str
    pdf_url: str
    published_at: datetime | None
    arxiv_updated_at: datetime | None
    discovered_at: datetime


class AnalysisRead(ORMModel):
    id: int
    paper_id: int
    profile_id: int
    kind: str
    status: str
    provider: str
    model: str
    transport: str
    content_source: str
    budget_mode: str
    usage_available: bool
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    summary: str
    verdict: str
    relevance_score: float | None
    novelty_score: float | None
    rigor_score: float | None
    impact_score: float | None
    strengths: list[str]
    weaknesses: list[str]
    tags: list[str]
    payload: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime


FeedbackAction = Literal["neutral", "save", "like", "dislike", "skip", "read"]
FeedbackReasonCode = Literal[
    "not_relevant",
    "incremental",
    "weak_evidence",
    "no_real_robot",
    "no_code",
    "duplicate",
    "poor_writing",
    "topic_pause",
]


class FeedbackWrite(BaseModel):
    action: FeedbackAction = "neutral"
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=5000)
    reasons: list[FeedbackReasonCode] = Field(default_factory=list)


class FeedbackRead(FeedbackWrite, ORMModel):
    id: int
    paper_id: int
    profile_id: int
    created_at: datetime
    updated_at: datetime


class PaperRead(ORMModel):
    id: int
    arxiv_id: str
    latest_version: int
    title: str
    abstract: str
    authors: list[str]
    affiliations: list[str]
    categories: list[str]
    primary_category: str | None
    comment: str | None
    journal_ref: str | None
    doi: str | None
    abs_url: str
    html_url: str
    pdf_url: str
    published_at: datetime | None
    arxiv_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    ingestion_source: str
    withdrawn: bool


class PaperDetail(PaperRead):
    versions: list[PaperVersionRead] = Field(default_factory=list)
    analyses: list[AnalysisRead] = Field(default_factory=list)
    feedback_items: list[FeedbackRead] = Field(default_factory=list)


class ScreeningResult(BaseModel):
    relevant: bool
    relevance_score: float = Field(ge=0, le=100)
    reason: str
    matched_topics: list[str]
    key_contributions: list[str]
    tags: list[str]

    @field_validator("relevance_score", mode="before")
    @classmethod
    def normalize_relevance_score(cls, value: Any) -> Any:
        if isinstance(value, int | float) and 0 < float(value) <= 1:
            return float(value) * 100
        return value


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

    @field_validator(
        "relevance_score", "novelty_score", "rigor_score", "impact_score", mode="before"
    )
    @classmethod
    def normalize_scores(cls, value: Any) -> Any:
        if isinstance(value, int | float) and 0 < float(value) <= 1:
            return float(value) * 100
        return value


class RecommendationRead(BaseModel):
    paper_id: int
    score: float = Field(ge=0, le=100)
    rank: int | None = None
    explanation: str
    matched_topics: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)


class RankedPaperRead(BaseModel):
    paper: PaperRead
    recommendation: RecommendationRead


class DigestRead(ORMModel):
    id: int
    profile_id: int
    digest_date: date
    title: str
    summary: str
    paper_ids: list[int]
    stats: dict[str, Any]
    status: str
    created_at: datetime


class JobRead(ORMModel):
    id: str
    kind: str
    status: str
    progress: int
    total: int
    message: str
    error: str | None
    result: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ProviderTestResult(BaseModel):
    ok: bool
    provider_name: str
    model: str
    transport: str
    message: str


class DashboardStats(BaseModel):
    fetched_today: int = 0
    relevant_today: int = 0
    analyzed_today: int = 0
    saved_total: int = 0


class DashboardRead(BaseModel):
    profile: ProfileRead
    stats: DashboardStats = Field(default_factory=DashboardStats)
    recommendations: list[RankedPaperRead] = Field(default_factory=list)
    latest_digest: DigestRead | None = None
    active_job: JobRead | None = None
    provider: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AnalysisRead",
    "DashboardRead",
    "DashboardStats",
    "DeepAnalysisResult",
    "DigestRead",
    "FeedbackAction",
    "FeedbackReasonCode",
    "FeedbackRead",
    "FeedbackWrite",
    "JobRead",
    "PaperDetail",
    "PaperRead",
    "PaperVersionRead",
    "ProfileRead",
    "ProfileUpdate",
    "ProviderTestResult",
    "RankedPaperRead",
    "RecommendationRead",
    "ScreeningResult",
    "TopicRead",
    "TopicWrite",
]
