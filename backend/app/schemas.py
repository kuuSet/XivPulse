"""Validated request, response, and model-output contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    "ProviderTestResult",
    "ScreeningResult",
]
