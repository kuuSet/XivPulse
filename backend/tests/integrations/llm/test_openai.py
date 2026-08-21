"""OpenAI-compatible provider adapter tests."""

from __future__ import annotations

import json

import httpx
import pytest
from app.integrations.llm.contracts import ProviderError, ProviderProtocolError
from app.integrations.llm.openai import OpenAICompatibleAdapter
from app.schemas import ScreeningResult
from app.settings import ProviderSettings


def _provider_settings(**overrides: object) -> ProviderSettings:
    values: dict[str, object] = {
        "provider_name": "test-provider",
        "api_base_url": "https://provider.test/v1",
        "model_name": "test-model",
        "api_protocol": "responses",
        "api_key": "test-secret",
    }
    values.update(overrides)
    return ProviderSettings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_responses_adapter_uses_strict_schema() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"score": 91}),
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(_provider_settings(), http_client=client)
        output = await adapter.generate_json_with_metadata(
            system_prompt="Return JSON.",
            user_prompt="screen it",
            response_model=ScreeningResult,
        )
    assert output.data.score == 91
    format_spec = captured[0]["text"]["format"]  # type: ignore[index]
    assert format_spec["type"] == "json_schema"
    assert format_spec["strict"] is True
    schema = format_spec["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.asyncio
async def test_adapter_records_responses_and_chat_token_usage() -> None:
    response_payload = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"score": 91}),
                    }
                ],
            }
        ],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 12},
            "total_tokens": 160,
        },
    }

    def responses_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responses_handler)) as client:
        result = await OpenAICompatibleAdapter(
            _provider_settings(), http_client=client
        ).generate_json_with_metadata(
            system_prompt="Return JSON.",
            user_prompt="screen",
            response_model=ScreeningResult,
        )
    assert result.usage.available is True
    assert result.usage.input_tokens == 120
    assert result.usage.cached_input_tokens == 30
    assert result.usage.output_tokens == 40
    assert result.usage.reasoning_tokens == 12
    assert result.usage.total_tokens == 160

    def chat_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 3000
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": response_payload["output"][0]["content"][0]["text"]}}
                ],
                "usage": {
                    "prompt_tokens": 90,
                    "prompt_tokens_details": {"cached_tokens": 20},
                    "completion_tokens": 25,
                    "completion_tokens_details": {"reasoning_tokens": 7},
                    "total_tokens": 115,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(chat_handler)) as client:
        result = await OpenAICompatibleAdapter(
            _provider_settings(api_protocol="chat"), http_client=client
        ).generate_json_with_metadata(
            system_prompt="Return JSON.",
            user_prompt="screen",
            response_model=ScreeningResult,
        )
    assert result.transport == "chat"
    assert result.usage.input_tokens == 90
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 25
    assert result.usage.reasoning_tokens == 7
    assert result.usage.total_tokens == 115


@pytest.mark.asyncio
async def test_responses_error_stops_without_calling_chat() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(404, json={"error": {"message": "not implemented"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"score": 10})}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAICompatibleAdapter(_provider_settings(), http_client=client)
        with pytest.raises(ProviderError, match="HTTP 404"):
            await adapter.generate_json_with_metadata(
                system_prompt="Return JSON.", user_prompt="screen", response_model=ScreeningResult
            )
    assert paths == ["/v1/responses"]

    def must_not_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("an unconfigured provider must remain offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(must_not_call)) as client:
        adapter = OpenAICompatibleAdapter(_provider_settings(api_key=None), http_client=client)
        with pytest.raises(ProviderError, match="No API key"):
            await adapter.generate_json_with_metadata(
                system_prompt="Return JSON.",
                user_prompt="screen",
                response_model=ScreeningResult,
            )


@pytest.mark.asyncio
async def test_invalid_responses_output_stops_without_calling_chat() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "not-json"}],
                        }
                    ],
                    "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"score": 80})}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderProtocolError, match="required schema"):
            await OpenAICompatibleAdapter(
                _provider_settings(), http_client=client
            ).generate_json_with_metadata(
                system_prompt="Return JSON.",
                user_prompt="screen",
                response_model=ScreeningResult,
            )
    assert paths == ["/v1/responses"]
