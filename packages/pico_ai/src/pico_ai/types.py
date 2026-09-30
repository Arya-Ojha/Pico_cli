"""Unified, provider-agnostic AI call types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """A tool the model may call, exposed as a JSON schema."""

    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: dict = Field(default_factory=dict)
    # Opaque replay token set by providers that require one (e.g. Gemini's
    # thoughtSignature, which must be returned verbatim in later requests or
    # the API rejects them). Ignored by all other providers.
    thought_signature: str | None = None


class Message(BaseModel):
    """A single conversation message."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class AICallRequest(BaseModel):
    """A provider-agnostic request: system prompt, messages, tools, model."""

    system: str = ""
    messages: list[Message] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    model: str = ""


class Usage(BaseModel):
    """Token usage for one response."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StreamEvent(BaseModel):
    """A single normalized streaming event."""

    kind: Literal["text", "thinking", "tool_call", "usage"]
    text: str = ""
    thinking: str = ""
    tool_call: ToolCall | None = None
    usage: Usage | None = None
