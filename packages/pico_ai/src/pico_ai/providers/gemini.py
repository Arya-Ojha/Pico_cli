"""Gemini provider: AI Studio wire format → app default ``StreamEvent``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from ..types import AICallRequest, StreamEvent, ToolCall, Usage
from .spec import FieldSpec, ProviderSpec

PROVIDER_ID = "gemini"
DISPLAY_NAME = "Gemini"
DESCRIPTION = "Google AI Studio (Gemini models)."
DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="api_key",
        label="API key",
        secret=True,
        required=True,
        env_var="GOOGLE_API_KEY",
        placeholder="AIza...",
    ),
    FieldSpec(
        key="base_url",
        label="Base URL",
        default=DEFAULT_BASE_URL,
        placeholder=DEFAULT_BASE_URL,
    ),
    FieldSpec(key="model", label="Default model", default=DEFAULT_MODEL),
)

# Fallback when the models endpoint is unreachable; any other id works via
# `/model <id>`.
CURATED_MODELS = ["gemini-2.0-flash", "gemini-1.5-pro"]


class GeminiProvider:
    """Google AI Studio adapter (``streamGenerateContent`` + SSE)."""

    provider_id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        first_token_timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout or httpx.Timeout(300.0, connect=10.0)
        self._first_token_timeout = first_token_timeout

    # -- streaming ----------------------------------------------------------

    async def stream(self, request: AICallRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(request)
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        calls: list[dict] = []  # buffered functionCall parts
        response_cm = client.stream(
            "POST",
            f"{self._base_url}/v1beta/models/{request.model}"
            ":streamGenerateContent?alt=sse",
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
                            f"Gemini error {response.status_code} "
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
                    self._prepend(first_line, lines), calls
                ):
                    yield event
                for i, call in enumerate(calls):
                    yield StreamEvent(
                        kind="tool_call",
                        tool_call=ToolCall(
                            id=f"call_{i}",
                            name=call["name"],
                            arguments=call["args"]
                            if isinstance(call["args"], dict)
                            else {},
                            thought_signature=call.get("thought_signature"),
                        ),
                    )
        finally:
            if response is not None:
                await response_cm.__aexit__(None, None, None)
            if self._client is None:
                await client.aclose()

    async def _emit_lines(
        self, lines: AsyncIterator[str], calls: list[dict]
    ) -> AsyncIterator[StreamEvent]:
        """Parse SSE chunks: text streams out, function calls buffer up."""
        async for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            for event in self._parse_chunk(json.loads(data), calls):
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
        """Return models from the API, falling back to curated ids."""
        headers = {"x-goog-api-key": self._api_key}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                response = await client.get(
                    f"{self._base_url}/v1beta/models",
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json().get("models", [])
            except httpx.HTTPError:
                data = []
        finally:
            if self._client is None:
                await client.aclose()
        models = [
            {
                "id": (entry.get("name") or "").removeprefix("models/"),
                "name": entry.get("displayName")
                or (entry.get("name") or "").removeprefix("models/"),
                "is_free": False,
                "supports_tools": True,
            }
            for entry in data
            if (entry.get("name") or "").startswith("models/gemini")
        ]
        if not models:
            models = [
                {"id": m, "name": m, "is_free": False, "supports_tools": True}
                for m in CURATED_MODELS
            ]
        return models

    # -- wire conversion ----------------------------------------------------

    def _build_payload(self, request: AICallRequest) -> dict:
        # Gemini requires strict user/model alternation, so consecutive
        # same-role parts merge into one message.
        contents: list[dict] = []

        def _push(role: str, parts: list[dict]) -> None:
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})

        if request.system:
            system = {"parts": [{"text": request.system}]}
        else:
            system = None
        for m in request.messages:
            if m.role == "tool":
                _push(
                    "user",
                    [
                        {
                            "functionResponse": {
                                "name": m.name or "",
                                "response": {"result": m.content},
                            }
                        }
                    ],
                )
            elif m.role == "assistant":
                parts: list[dict] = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    part: dict = {
                        "functionCall": {"name": tc.name, "args": tc.arguments}
                    }
                    # Replay verbatim: Gemini 3+ 400s when the first
                    # functionCall part of a step lacks its signature.
                    if tc.thought_signature:
                        part["thoughtSignature"] = tc.thought_signature
                    parts.append(part)
                _push("model", parts)
            else:
                _push("user", [{"text": m.content}])
        payload: dict = {"contents": contents}
        if system is not None:
            payload["system_instruction"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema or {"type": "object"},
                        }
                        for t in request.tools
                    ]
                }
            ]
        return payload

    def _parse_chunk(self, chunk: dict, calls: list[dict]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for candidate in chunk.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    events.append(StreamEvent(kind="text", text=part["text"]))
                call = part.get("functionCall")
                if call:
                    # Args may stream split across chunks; merge per name.
                    # The sibling thoughtSignature (Gemini 3+) must be
                    # replayed verbatim later or the API 400s — capture it.
                    signature = part.get("thoughtSignature") or part.get(
                        "thought_signature"
                    )
                    existing = next(
                        (c for c in calls if c["name"] == call.get("name")),
                        None,
                    )
                    if existing is None:
                        calls.append(
                            {
                                "name": call.get("name", ""),
                                "args": call.get("args", {}),
                                "thought_signature": signature,
                            }
                        )
                    else:
                        if isinstance(call.get("args"), dict):
                            existing["args"].update(call["args"])
                        if not existing.get("thought_signature"):
                            existing["thought_signature"] = signature
        usage = chunk.get("usageMetadata") or {}
        if usage:
            prompt = usage.get("promptTokenCount", 0)
            completion = usage.get("candidatesTokenCount", 0)
            events.append(
                StreamEvent(
                    kind="usage",
                    usage=Usage(
                        input_tokens=prompt,
                        output_tokens=completion,
                        total_tokens=usage.get("totalTokenCount", prompt + completion),
                    ),
                )
            )
        return events


def create(config: dict[str, str]) -> GeminiProvider:
    """Build the adapter from a setup-form config dict."""
    return GeminiProvider(
        api_key=config.get("api_key", ""),
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
