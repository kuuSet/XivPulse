"""Structured-output client for OpenAI-compatible model providers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from ..schemas import ProviderTestResult
from ..settings import ProviderSettings, get_process_settings

TModel = TypeVar("TModel", bound=BaseModel)


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


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass


@dataclass(slots=True)
class GenerationResult[TModel: BaseModel]:
    data: TModel
    transport: Literal["responses", "chat"]
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    prompt_metadata: dict[str, Any] = field(default_factory=dict)


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Provider base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Provider base URL must not contain credentials")
    return value.strip().rstrip("/")


def _strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
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


def _schema_name(model: type[BaseModel]) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]", "_", model.__name__).strip("_")
    return (value or "structured_response")[:64]


def _parse_json_text(value: str) -> Any:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ProviderProtocolError("Provider returned invalid JSON output") from exc


def _responses_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(content, Mapping):
                continue
            if content.get("type") == "refusal":
                raise ProviderProtocolError("Provider refused the analysis request")
            value = content.get("text")
            if isinstance(value, str) and value.strip():
                return value
    status = payload.get("status")
    if status == "incomplete":
        raise ProviderProtocolError("Provider response was incomplete")
    raise ProviderProtocolError("Responses endpoint returned no text output")


def _chat_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ProviderProtocolError("Chat endpoint returned no choices")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ProviderProtocolError("Chat endpoint returned no message")
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        raise ProviderProtocolError("Provider refused the analysis request")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text"))
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        if parts:
            return "".join(parts)
    raise ProviderProtocolError("Chat endpoint returned no text output")


def _usage_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _provider_usage(payload: Mapping[str, Any], transport: str) -> TokenUsage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return TokenUsage()
    if transport == "responses":
        input_details = usage.get("input_tokens_details")
        output_details = usage.get("output_tokens_details")
        return TokenUsage(
            input_tokens=_usage_int(usage.get("input_tokens")),
            cached_input_tokens=_usage_int(
                input_details.get("cached_tokens") if isinstance(input_details, Mapping) else None
            ),
            output_tokens=_usage_int(usage.get("output_tokens")),
            reasoning_tokens=_usage_int(
                output_details.get("reasoning_tokens")
                if isinstance(output_details, Mapping)
                else None
            ),
            total_tokens=_usage_int(usage.get("total_tokens")),
        )
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    return TokenUsage(
        input_tokens=_usage_int(usage.get("prompt_tokens")),
        cached_input_tokens=_usage_int(
            prompt_details.get("cached_tokens") if isinstance(prompt_details, Mapping) else None
        ),
        output_tokens=_usage_int(usage.get("completion_tokens")),
        reasoning_tokens=_usage_int(
            completion_details.get("reasoning_tokens")
            if isinstance(completion_details, Mapping)
            else None
        ),
        total_tokens=_usage_int(usage.get("total_tokens")),
    )


class OpenAICompatibleAdapter:
    """Responses-first adapter with a Chat Completions compatibility fallback."""

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_process_settings().provider
        self.base_url = _safe_base_url(self.settings.api_base_url)
        self._client = http_client
        self.last_transport: Literal["responses", "chat"] = self.settings.api_protocol

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
        schema_name: str | None = None,
        max_output_tokens: int = 3000,
    ) -> TModel:
        result = await self.generate_json_with_metadata(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            schema_name=schema_name,
            max_output_tokens=max_output_tokens,
        )
        return result.data

    async def generate_json_with_metadata(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
        schema_name: str | None = None,
        max_output_tokens: int = 3000,
    ) -> GenerationResult[TModel]:
        if not self.settings.configured:
            raise ProviderError("No API key is configured")

        schema_name = schema_name or _schema_name(response_model)
        schema = _strict_json_schema(response_model)
        common_input = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self.settings.api_protocol == "chat":
            data, usage = await self._call_chat(
                common_input, response_model, schema_name, schema, max_output_tokens
            )
            transport: Literal["responses", "chat"] = "chat"
        else:
            try:
                data, usage = await self._call_responses(
                    common_input, response_model, schema_name, schema, max_output_tokens
                )
                transport = "responses"
            except _FallbackToChat as fallback:
                data, chat_usage = await self._call_chat(
                    common_input, response_model, schema_name, schema, max_output_tokens
                )
                usage = fallback.usage.plus(chat_usage)
                transport = "chat"
        self.last_transport = transport
        return GenerationResult(
            data=data,
            transport=transport,
            provider=self.settings.provider_name,
            model=self.settings.model_name,
            usage=usage,
        )

    async def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            if self._client is not None:
                response = await self._client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.api_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self.settings.api_timeout_seconds,
                    )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderError("Could not reach the configured model provider") from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("The configured API key was rejected")
        if response.status_code == 429:
            raise ProviderRateLimitError("The model provider rate limit was reached")
        if response.status_code >= 400:
            raise _ProviderHTTPError(response.status_code)
        try:
            value = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderProtocolError("Provider returned a non-JSON response") from exc
        if not isinstance(value, dict):
            raise ProviderProtocolError("Provider returned an unexpected response shape")
        return value

    async def _call_responses(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> tuple[TModel, TokenUsage]:
        payload = {
            "model": self.settings.model_name,
            "input": messages,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": min(max(128, max_output_tokens), 16000),
        }
        raw: dict[str, Any] | None = None
        try:
            raw = await self._request("responses", payload)
            output = _parse_json_text(_responses_text(raw))
            return response_model.model_validate(output), _provider_usage(raw, "responses")
        except _ProviderHTTPError as exc:
            if exc.status_code in {400, 404, 405, 415, 422, 501}:
                raise _FallbackToChat() from exc
            raise ProviderError(f"Model provider returned HTTP {exc.status_code}") from exc
        except (ProviderProtocolError, ValidationError) as exc:
            # Several OpenAI-compatible providers expose /responses but only
            # partially implement its output format; Chat may still be valid.
            raise _FallbackToChat(
                _provider_usage(raw, "responses") if raw is not None else TokenUsage()
            ) from exc

    async def _call_chat(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> tuple[TModel, TokenUsage]:
        payload = {
            "model": self.settings.model_name,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_completion_tokens": min(max(128, max_output_tokens), 16000),
        }
        try:
            try:
                raw = await self._request("chat/completions", payload)
            except _ProviderHTTPError as exc:
                if exc.status_code not in {400, 422}:
                    raise
                # Older OpenAI-compatible servers may only implement the
                # legacy field. A rejected request has not generated tokens.
                legacy_payload = dict(payload)
                legacy_payload["max_tokens"] = legacy_payload.pop("max_completion_tokens")
                raw = await self._request("chat/completions", legacy_payload)
            output = _parse_json_text(_chat_text(raw))
            return response_model.model_validate(output), _provider_usage(raw, "chat")
        except _ProviderHTTPError as exc:
            raise ProviderError(f"Model provider returned HTTP {exc.status_code}") from exc
        except ValidationError as exc:
            raise ProviderProtocolError(
                "Provider output did not match the required schema"
            ) from exc

    async def test_connection(self) -> ProviderTestResult:
        if not self.settings.configured:
            return ProviderTestResult(
                ok=False,
                provider_name=self.settings.provider_name,
                model=self.settings.model_name,
                transport=self.settings.api_protocol,
                message="未配置 API Key。",
            )

        class Ping(BaseModel):
            ok: bool

        try:
            result = await self.generate_json_with_metadata(
                system_prompt="Return the requested JSON and nothing else.",
                user_prompt='Return {"ok": true}.',
                response_model=Ping,
                max_output_tokens=128,
            )
        except ProviderError as exc:
            return ProviderTestResult(
                ok=False,
                provider_name=self.settings.provider_name,
                model=self.settings.model_name,
                transport=self.last_transport,
                message=str(exc),
            )
        return ProviderTestResult(
            ok=result.data.ok,
            provider_name=result.provider,
            model=result.model,
            transport=result.transport,
            message="模型接口连接成功。",
        )


class _FallbackToChat(Exception):
    def __init__(self, usage: TokenUsage | None = None) -> None:
        self.usage = usage or TokenUsage()
        super().__init__("fall back to Chat Completions")


class _ProviderHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")
