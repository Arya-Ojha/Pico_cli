"""Shared base for OpenAI chat-completions-compatible providers.

Covers any backend speaking ``POST {base}/chat/completions`` with SSE
``data:`` chunks shaped like OpenAI's (``choices[].delta``,
``finish_reason``, ``usage``). Subclasses only set endpoint defaults and
identity — the wire conversion lives here, once.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from ..types import AICallRequest, StreamEvent, ToolCall, Usage


class OpenAICompatProvider:
    """Streams chat completions and normalizes them to ``StreamEvent``."""

    provider_id: str = "openai-compat"
    display_name: str = "OpenAI-compatible"
    completions_path: str = "/chat/completions"
    models_path: str = "/models"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        first_token_timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout or httpx.Timeout(300.0, connect=10.0)
        self._first_token_timeout = first_token_timeout
        self._extra_headers = extra_headers or {}

    # -- streaming ----------------------------------------------------------

    async def stream(self, request: AICallRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(request)
        headers = {
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        pending: dict[int, dict] = {}
        response_cm = client.stream(
            "POST",
            f"{self._base_url}{self.completions_path}",
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
                            f"{self.display_name} error {response.status_code} "
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
                async for event in self._emit_lines(
                    self._prepend(first_line, lines), pending
                ):
                    yield event
        finally:
            if response is not None:
                await response_cm.__aexit__(None, None, None)
            if self._client is None:
                await client.aclose()

    async def _emit_lines(
        self, lines: AsyncIterator[str], pending: dict[int, dict]
    ) -> AsyncIterator[StreamEvent]:
        """Parse SSE lines and yield normalized stream events."""
        async for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            for event in self._parse_chunk(json.loads(data), pending):
                yield event

    @staticmethod
    async def _prepend(
        first: str, rest: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        yield first
        async for item in rest:
            yield item

    # -- models -------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """Return available models (OpenAI ``/models`` shape)."""
        headers = dict(self._extra_headers)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(
                f"{self._base_url}{self.models_path}",
                headers=headers,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
        finally:
            if self._client is None:
                await client.aclose()
        return [
            {
                "id": entry.get("id", ""),
                "name": entry.get("id", ""),
                "is_free": False,
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
            msg: dict = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
                if not m.content:
                    msg["content"] = None
            if m.tool_call_id is not None:
                msg["tool_call_id"] = m.tool_call_id
            if m.name is not None:
                msg["name"] = m.name
            messages.append(msg)
        payload: dict = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        return payload

    def _parse_chunk(self, chunk: dict, pending: dict[int, dict]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                events.append(StreamEvent(kind="text", text=content))
            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
            if reasoning:
                events.append(StreamEvent(kind="thinking", thinking=reasoning))
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                entry = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    entry["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    entry["name"] = fn["name"]
                if fn.get("arguments"):
                    entry["args"] += fn["arguments"]
            if choice.get("finish_reason") == "tool_calls":
                for idx in sorted(pending):
                    entry = pending[idx]
                    events.append(
                        StreamEvent(
                            kind="tool_call",
                            tool_call=ToolCall(
                                id=entry["id"],
                                name=entry["name"],
                                arguments=self._parse_args(entry["args"]),
                            ),
                        )
                    )
                pending.clear()
        usage = chunk.get("usage")
        if usage:
            events.append(
                StreamEvent(
                    kind="usage",
                    usage=Usage(
                        input_tokens=usage.get("prompt_tokens", 0),
                        output_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                    ),
                )
            )
        return events

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
