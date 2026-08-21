"""JSON-ready model analysis views."""

from __future__ import annotations

from typing import Any

from ...models import Analysis


def _analysis_wire(analysis: Analysis | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    payload = analysis.payload
    return {
        "kind": analysis.kind,
        "overview": payload.get("problem") or analysis.summary,
        "contributions": payload.get("key_contributions", []),
        "method": "；".join(payload.get("methods", [])),
        "evidence": "；".join(payload.get("evidence", [])),
        "limitations": payload.get("limitations", analysis.weaknesses),
        "advocate": "；".join(analysis.strengths),
        "skeptic": "；".join(analysis.weaknesses),
        "input_scope": analysis.content_source,
        "budget_mode": analysis.budget_mode,
        "token_usage": {
            "available": analysis.usage_available,
            "input_tokens": analysis.input_tokens,
            "cached_input_tokens": analysis.cached_input_tokens,
            "output_tokens": analysis.output_tokens,
            "reasoning_tokens": analysis.reasoning_tokens,
            "total_tokens": analysis.total_tokens,
        },
        "relevance_score": analysis.relevance_score,
        "novelty_score": analysis.novelty_score,
        "method_score": analysis.rigor_score,
        "evidence_score": analysis.rigor_score,
        "potential_score": analysis.impact_score,
        "tags": analysis.tags,
    }
