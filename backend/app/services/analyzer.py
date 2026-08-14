"""Structured paper screening and analysis over OpenAI-compatible APIs."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy.orm import Session

from ..config import AppConfig, get_config
from ..models import Analysis, Paper, Profile, Topic
from ..schemas import DeepAnalysisResult, ProviderTestResult, ScreeningResult
from .arxiv import DEFAULT_USER_AGENT, parse_arxiv_id, polite_wait
from .direction_matching import (
    canonical_requested_direction_names,
    direction_evidence,
    terms,
)
from .direction_taxonomy import stamp_direction_attribution
from .prompt_catalog import (
    DEEP_ANALYSIS_SYSTEM_PROMPT,
    DEFAULT_PROMPT_TEMPLATE_ID,
    SCREENING_SYSTEM_PROMPT,
    analysis_prompt_metadata,
    prompt_template,
)

TModel = TypeVar("TModel", bound=BaseModel)


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
    transport: Literal["responses", "chat", "mock"]
    provider: str
    model: str
    mocked: bool = False
    usage: TokenUsage = field(default_factory=TokenUsage)
    prompt_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FullTextDocument:
    content: str
    source: Literal["html", "pdf", "unavailable"]
    url: str | None
    truncated: bool = False
    error: str | None = None


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
        config: AppConfig | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or get_config()
        self.base_url = _safe_base_url(self.config.base_url)
        self._client = http_client
        self.last_transport: Literal["responses", "chat", "mock"] = "mock"

    @property
    def mock_mode(self) -> bool:
        return self.config.mock_mode

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[TModel],
        schema_name: str | None = None,
        max_output_tokens: int = 3000,
        mock_factory: Callable[[], TModel] | None = None,
    ) -> TModel:
        result = await self.generate_json_with_metadata(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            schema_name=schema_name,
            max_output_tokens=max_output_tokens,
            mock_factory=mock_factory,
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
        mock_factory: Callable[[], TModel] | None = None,
    ) -> GenerationResult[TModel]:
        if self.mock_mode:
            if mock_factory is None:
                raise ProviderError("No API key is configured and no mock result was supplied")
            self.last_transport = "mock"
            return GenerationResult(
                data=mock_factory(),
                transport="mock",
                provider="mock",
                model="mock",
                mocked=True,
            )

        schema_name = schema_name or _schema_name(response_model)
        schema = _strict_json_schema(response_model)
        common_input = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self.config.api_mode == "chat":
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
            provider=self.config.provider_name,
            model=self.config.model,
            usage=usage,
        )

    async def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            if self._client is not None:
                response = await self._client.post(
                    url, headers=headers, json=payload, timeout=self.config.request_timeout
                )
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.post(
                        url, headers=headers, json=payload, timeout=self.config.request_timeout
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
            "model": self.config.model,
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
            "model": self.config.model,
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
        if self.mock_mode:
            return ProviderTestResult(
                ok=True,
                provider_name="mock",
                model="mock",
                transport="mock",
                message="未检测到 API key，当前使用本地演示分析。",
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
                provider_name=self.config.provider_name,
                model=self.config.model,
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


class FullTextFetcher:
    """Fetch arXiv HTML first and fall back to bounded PDF extraction."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 45.0,
        rate_limit_seconds: float = 3.0,
        max_chars: int = 45_000,
        max_pdf_bytes: int = 35 * 1024 * 1024,
        max_pdf_pages: int = 40,
    ) -> None:
        self._client = http_client
        self.timeout = timeout
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.max_chars = max(5_000, max_chars)
        self.max_pdf_bytes = max(1024 * 1024, max_pdf_bytes)
        self.max_pdf_pages = max(1, max_pdf_pages)

    async def _get(self, url: str, accept: str) -> httpx.Response:
        await polite_wait(self.rate_limit_seconds)
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": accept}
        if self._client is not None:
            response = await self._client.get(url, headers=headers, timeout=self.timeout)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response

    async def fetch(self, paper: Paper | str) -> FullTextDocument:
        raw_id = paper.arxiv_id if isinstance(paper, Paper) else paper
        normalized = parse_arxiv_id(raw_id)
        if isinstance(paper, Paper):
            version = max(1, int(paper.latest_version or normalized.version))
        else:
            version = normalized.version
        versioned = f"{normalized.base_id}v{version}"
        encoded = quote(versioned, safe="/.")
        html_url = f"https://arxiv.org/html/{encoded}"
        pdf_url = f"https://arxiv.org/pdf/{encoded}"
        errors: list[str] = []

        try:
            response = await self._get(html_url, "text/html,application/xhtml+xml")
            content = self._extract_html(response.text)
            if len(content) >= 500:
                content, truncated = self._truncate(content)
                return FullTextDocument(content, "html", html_url, truncated)
            errors.append("HTML 正文过短")
        except (httpx.HTTPError, UnicodeError, ValueError) as exc:
            errors.append(f"HTML 不可用 ({type(exc).__name__})")

        try:
            response = await self._get(pdf_url, "application/pdf")
            if len(response.content) > self.max_pdf_bytes:
                raise ValueError("PDF exceeded size limit")
            content = await asyncio.to_thread(self._extract_pdf, response.content)
            if not content.strip():
                raise ValueError("PDF contained no extractable text")
            content, truncated = self._truncate(content)
            return FullTextDocument(content, "pdf", pdf_url, truncated)
        except (httpx.HTTPError, OSError, UnicodeError, ValueError, PyPdfError) as exc:
            errors.append(f"PDF 不可用 ({type(exc).__name__})")
        return FullTextDocument("", "unavailable", None, error="；".join(errors))

    @staticmethod
    def _extract_html(payload: str) -> str:
        soup = BeautifulSoup(payload, "html.parser")
        for element in soup.select("script, style, nav, header, footer, aside, .ltx_page_footer"):
            element.decompose()
        root = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        return re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True)).strip()

    def _extract_pdf(self, payload: bytes) -> str:
        reader = PdfReader(BytesIO(payload))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # pypdf raises provider-specific exceptions
                raise ValueError("Encrypted PDF could not be read") from exc
        parts: list[str] = []
        for page in reader.pages[: self.max_pdf_pages]:
            parts.append(page.extract_text() or "")
            if sum(len(part) for part in parts) >= self.max_chars:
                break
        return re.sub(r"\n{3,}", "\n\n", "\n\n".join(parts)).strip()

    def _truncate(self, content: str) -> tuple[str, bool]:
        if len(content) <= self.max_chars:
            return content, False
        return content[: self.max_chars].rsplit("\n", 1)[0].strip(), True


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


# Concise aliases for route modules.
Analyzer = PaperAnalyzer
OpenAIAdapter = OpenAICompatibleAdapter


__all__ = [
    "Analyzer",
    "FullTextDocument",
    "FullTextFetcher",
    "GenerationResult",
    "ANALYSIS_BUDGETS",
    "AnalysisBudget",
    "TokenUsage",
    "analysis_budget",
    "OpenAIAdapter",
    "OpenAICompatibleAdapter",
    "PaperAnalyzer",
    "ProviderAuthenticationError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderRateLimitError",
]
