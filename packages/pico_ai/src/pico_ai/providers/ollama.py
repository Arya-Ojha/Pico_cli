"""Ollama provider: local ``/api/chat`` NDJSON stream → app default ``StreamEvent``.

No API key needed — just a reachable Ollama server (local or remote).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from ..types import AICallRequest, StreamEvent, ToolCall, Usage
from .spec import FieldSpec, ProviderSpec

PROVIDER_ID = "ollama"
DISPLAY_NAME = "Ollama"
DESCRIPTION = "Local Ollama server (no API key)."
DEFAULT_MODEL = "llama3.1"
DEFAULT_BASE_URL = "http://localhost:11434"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="base_url",
        label="Base URL",
        default=DEFAULT_BASE_URL,
        env_var="OLLAMA_HOST",
        placeholder=DEFAULT_BASE_URL,
    ),
    FieldSpec(key="model", label="Default model", default=DEFAULT_MODEL),
)


class OllamaProvider:
    """Ollama ``/api/chat`` adapter (newline-delimited JSON stream)."""

    provider_id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        first_token_timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout or httpx.Timeout(300.0, connect=10.0)
        self._first_token_timeout = first_token_timeout

    # -- streaming ----------------------------------------------------------

    async def stream(self, request: AICallRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(request)
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        pending: dict[int, dict] = {}
        prompt_eval = 0
        eval_count = 0
        response_cm = client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json=payload,
        )
        response: httpx.Response | None = None
        try:
            try:
                async with asyncio.timeout(self._first_token_timeout):
                    response = await response_cm.__aenter__()
                    if response.status_code >= 400:
                        body = (await response.aread()).decode(errors="replace")
                        raise RuntimeError(
                            f"Ollama error {response.status_code} "
                            f"for model '{request.model}': "
                            f"{body[:500]}"
                        )
                    lines = response.aiter_lines()
                    first_line = await anext(lines, None)
            except TimeoutError as exc:
                raise RuntimeError(
                    f"no response from model within "
                    f"{self._first_token_timeout:g}s "
                    f"(first-token timeout); is the Ollama server running?"
                ) from exc
            if first_line is not None:
                async for line in self._prepend(first_line, lines):
                    if not line.strip():
                        continue
                    for event in self._parse_line(json.loads(line), pending):
                        if event.kind == "usage" and event.usage is not None:
                            prompt_eval = event.usage.input_tokens
                            eval_count = event.usage.output_tokens
                        else:
                            yield event
                for idx in sorted(pending):
                    entry = pending[idx]
                    yield StreamEvent(
                        kind="tool_call",
                        tool_call=ToolCall(
                            id=f"call_{idx}",
                            name=entry["name"],
                            arguments=entry["args"]
                            if isinstance(entry["args"], dict)
                            else {},
                        ),
                    )
                pending.clear()
                yield StreamEvent(
                    kind="usage",
                    usage=Usage(
                        input_tokens=prompt_eval,
                        output_tokens=eval_count,
                        total_tokens=prompt_eval + eval_count,
                    ),
                )
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

    # -- models -------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """Return locally pulled models from ``/api/tags``."""
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            data = response.json().get("models", [])
        finally:
            if self._client is None:
                await client.aclose()
        return [
            {
                "id": entry.get("name", ""),
                "name": entry.get("name", ""),
                "is_free": True,
                "supports_tools": True,
            }
            for entry in data
        ]

    # -- wire conversion ----------------------------------------------------

    def _build_payload(self, request: AICallRequest) -> dict:
        messages: list[dict] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            if m.role == "assistant" and m.tool_calls:
                msg: dict = {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in m.tool_calls
                    ],
                }
            else:
                msg = {"role": m.role, "content": m.content}
            messages.append(msg)
        payload: dict = {
            "model": request.model,
            "messages": messages,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema or {"type": "object"},
                    },
                }
                for t in request.tools
            ]
        return payload

    def _parse_line(self, chunk: dict, pending: dict[int, dict]) -> list[StreamEvent]:
        # Terminal chunk carries counters, not content.
        if chunk.get("done"):
            return [
                StreamEvent(
                    kind="usage",
                    usage=Usage(
                        input_tokens=chunk.get("prompt_eval_count", 0),
                        output_tokens=chunk.get("eval_count", 0),
                        total_tokens=chunk.get("prompt_eval_count", 0)
                        + chunk.get("eval_count", 0),
                    ),
                )
            ]
        events: list[StreamEvent] = []
        message = chunk.get("message") or {}
        if message.get("content"):
            events.append(StreamEvent(kind="text", text=message["content"]))
        for idx, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            entry = pending.setdefault(idx, {"name": "", "args": {}})
            if fn.get("name"):
                entry["name"] = fn["name"]
            if isinstance(fn.get("arguments"), dict):
                if isinstance(entry["args"], dict):
                    entry["args"].update(fn["arguments"])
                else:
                    entry["args"] = fn["arguments"]
        return events


def create(config: dict[str, str]) -> OllamaProvider:
    """Build the adapter from a setup-form config dict."""
    return OllamaProvider(
        base_url=config.get("base_url", "") or DEFAULT_BASE_URL,
    )


SPEC = ProviderSpec(
    id=PROVIDER_ID,
    display_name=DISPLAY_NAME,
    description=DESCRIPTION,
    fields=FIELDS,
    default_model=DEFAULT_MODEL,
    create=create,
)
