"""Contracts shared by structured model transports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from ...schemas import ProviderTestResult


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def available(self) -> bool:
        return any(
            value is not None
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        )

    def analysis_fields(self, budget_mode: str) -> dict[str, Any]:
        return {
            "budget_mode": budget_mode,
            "usage_available": self.available,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }

    def plus(self, other: TokenUsage) -> TokenUsage:
        def add(left: int | None, right: int | None) -> int | None:
            if left is None and right is None:
                return None
            return (left or 0) + (right or 0)

        return TokenUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            cached_input_tokens=add(self.cached_input_tokens, other.cached_input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
            reasoning_tokens=add(self.reasoning_tokens, other.reasoning_tokens),
            total_tokens=add(self.total_tokens, other.total_tokens),
        )


class ProviderError(RuntimeError):
    """A sanitized provider error safe to display in the local UI."""


class ProviderProtocolError(ProviderError):
    pass


@dataclass(slots=True)
class GenerationResult[TModel: BaseModel]:
    data: TModel
    transport: Literal["responses", "chat", "codex"]
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    prompt_metadata: dict[str, Any] = field(default_factory=dict)


class StructuredModelAdapter(Protocol):
    async def generate_json_with_metadata[TModel: BaseModel](
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
        schema_name: str | None = None,
        max_output_tokens: int = 3000,
    ) -> GenerationResult[TModel]: ...

    async def test_connection(self) -> ProviderTestResult: ...


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert Pydantic output to the strict object subset expected by Responses."""

    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                if isinstance(properties, dict):
                    node["required"] = list(properties)
                node["additionalProperties"] = False
            for value in list(node.values()):
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema
