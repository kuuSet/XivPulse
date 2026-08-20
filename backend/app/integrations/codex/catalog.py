"""Codex model and reasoning choices exposed by XivPulse."""

from __future__ import annotations

from typing import Literal

CodexModel = Literal["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
CodexReasoningEffort = Literal["low", "medium", "high", "xhigh"]

DEFAULT_CODEX_MODEL: CodexModel = "gpt-5.6-sol"
DEFAULT_CODEX_REASONING_EFFORT: CodexReasoningEffort = "medium"

CODEX_MODEL_OPTIONS = (
    {
        "value": "gpt-5.6-sol",
        "label": "GPT-5.6 Sol",
        "description": "旗舰模型，适合复杂研究、代码和多步骤分析。",
    },
    {
        "value": "gpt-5.6-terra",
        "label": "GPT-5.6 Terra",
        "description": "能力、速度与成本更均衡，适合日常论文筛选。",
    },
    {
        "value": "gpt-5.6-luna",
        "label": "GPT-5.6 Luna",
        "description": "速度更快、成本更低，适合大批量初筛。",
    },
)

CODEX_REASONING_OPTIONS = (
    {"value": "low", "label": "Low", "description": "最快，适合明确的批量任务。"},
    {"value": "medium", "label": "Medium", "description": "速度与分析深度均衡。"},
    {"value": "high", "label": "High", "description": "加强规划、分析和检查。"},
    {
        "value": "xhigh",
        "label": "Extra High",
        "description": "最深推理；是否支持取决于所选模型。",
    },
)


__all__ = [
    "CODEX_MODEL_OPTIONS",
    "CODEX_REASONING_OPTIONS",
    "DEFAULT_CODEX_MODEL",
    "DEFAULT_CODEX_REASONING_EFFORT",
    "CodexModel",
    "CodexReasoningEffort",
]
