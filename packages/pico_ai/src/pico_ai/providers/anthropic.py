"""Anthropic provider: Messages API wire format → app default ``StreamEvent``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from ..types import AICallRequest, StreamEvent, ToolCall, Usage
from .spec import FieldSpec, ProviderSpec

PROVIDER_ID = "anthropic"
DISPLAY_NAME = "Anthropic"
DESCRIPTION = "Anthropic Messages API (Claude models)."
DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="api_key",
        label="API key",
        secret=True,
        required=True,
        env_var="ANTHROPIC_API_KEY",
        placeholder="sk-ant-...",
    ),
    FieldSpec(
        key="base_url",
        label="Base URL",
        default=DEFAULT_BASE_URL,
        placeholder=DEFAULT_BASE_URL,
    ),
    FieldSpec(key="model", label="Default model", default=DEFAULT_MODEL),
    FieldSpec(key="max_tokens", label="Max output tokens", default="4096"),
)

# Curated fallback: Anthropic exposes no list-models endpoint, so the model
# picker shows these; any other id works via `/model <id>`.
CURATED_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-haiku-3-5-20241022",
]


class AnthropicProvider:
    """Anthropic Messages API adapter."""

    provider_id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = 4096,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        first_token_timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._client = client
        self._timeout = timeout or httpx.Timeout(300.0, connect=10.0)
        self._first_token_timeout = first_token_timeout

    # -- streaming ----------------------------------------------------------

    async def stream(self, request: AICallRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(request)
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        response_cm = client.stream(
            "POST",
            f"{self._base_url}/v1/messages",
            json=payload,
            headers=headers,
        )
        response: httpx.Response | None = None
        try:
            try:
                async with asyncio.timeout(self._first_token_timeout):
                    response = await response_cm.__aenter__()
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")
                        raise RuntimeError(
                            f"Anthropic error {response.status_code} "
                            f"for model '{request.model}': "
                            f"{body[:500]}"
                        )
                    lines = response.aiter_lines()
                    first_line = await anext(lines, None)
            except TimeoutError as exc:
                raise RuntimeError(
                    f"no response from model within "
                    f"{self._first_token_timeout:g}s "
                    f"(first-token timeout); try another model"
                ) from exc
            if first_line is not None:
                async for event in self._parse_sse(
                    self._prepend(first_line, lines)
                ):
                    yield event
        finally:
            if response is not None:
                await response_cm.__aexit__(None, None, None)
            if self._client is None:
                await client.aclose()

    @staticmethod
    async def _prepend(
        first: str, rest: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        yield first
        async for item in rest:
            yield item

    async def _parse_sse(
        self, lines: AsyncIterator[str]
    ) -> AsyncIterator[StreamEvent]:
        """Pair ``event:``/``data:`` lines into normalized stream events."""
        pending: dict[int, dict] = {}
        input_tokens = 0
        output_tokens = 0
        event_type = "message"
        async for line in lines:
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = json.loads(line[len("data:") :].strip())
            if event_type == "ping":
                continue
            if event_type == "error":
                raise RuntimeError(
                    f"Anthropic error: {data.get('message', data)}"
                )
            if event_type == "message_start":
                input_tokens = (data.get("message", {}).get("usage") or {}).get(
                    "input_tokens", 0
                )
            elif event_type == "content_block_start":
                block = data.get("content_block") or {}
                if block.get("type") == "tool_use":
                    pending[data.get("index", 0)] = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "json": "",
                    }
            elif event_type == "content_block_delta":
                delta = data.get("delta") or {}
                dtype = delta.get("type", "")
                if dtype == "text_delta" and delta.get("text"):
                    yield StreamEvent(kind="text", text=delta["text"])
                elif dtype == "input_json_delta":
                    entry = pending.setdefault(
                        data.get("index", 0), {"id": "", "name": "", "json": ""}
                    )
                    entry["json"] += delta.get("partial_json", "")
            elif event_type == "message_delta":
                output_tokens = (data.get("usage") or {}).get("output_tokens", 0)
            elif event_type == "message_stop":
                for idx in sorted(pending):
                    entry = pending[idx]
                    yield StreamEvent(
                        kind="tool_call",
                        tool_call=ToolCall(
                            id=entry["id"],
                            name=entry["name"],
                            arguments=self._parse_args(entry["json"]),
                        ),
                    )
                pending.clear()
                yield StreamEvent(
                    kind="usage",
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                    ),
                )

    # -- models -------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """Curated model list (Anthropic exposes no list endpoint)."""
        return [
            {"id": m, "name": m, "is_free": False, "supports_tools": True}
            for m in CURATED_MODELS
        ]

    # -- wire conversion ----------------------------------------------------

    def _build_payload(self, request: AICallRequest) -> dict:
        messages: list[dict] = []
        for m in request.messages:
            if m.role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                    for tc in m.tool_calls
                )
                messages.append({"role": "assistant", "content": blocks})
            else:
                role = "assistant" if m.role == "assistant" else "user"
                messages.append({"role": role, "content": m.content})
        payload: dict = {
            "model": request.model,
            "max_tokens": self._max_tokens,
            "messages": messages,
            "stream": True,
        }
        if request.system:
            payload["system"] = request.system
        if request.tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema or {"type": "object"},
                }
                for t in request.tools
            ]
        return payload

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def create(config: dict[str, str]) -> AnthropicProvider:
    """Build the adapter from a setup-form config dict."""
    try:
        max_tokens = int(config.get("max_tokens", "") or 4096)
    except ValueError:
        max_tokens = 4096
    return AnthropicProvider(
        api_key=config.get("api_key", ""),
        base_url=config.get("base_url", "") or DEFAULT_BASE_URL,
        max_tokens=max_tokens,
    )


SPEC = ProviderSpec(
    id=PROVIDER_ID,
    display_name=DISPLAY_NAME,
    description=DESCRIPTION,
    fields=FIELDS,
    default_model=DEFAULT_MODEL,
    create=create,
)
