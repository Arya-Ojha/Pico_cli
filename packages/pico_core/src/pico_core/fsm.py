"""The explicit finite-state-machine agent loop.

States::

    idle → streaming ⇄ tool_executing → done
             ↓
          compacting          (triggered by token threshold)
    error                     (reachable from any state)

Yolo mode means there is no approval/confirmation state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from enum import Enum
from typing import Literal, Protocol

from pydantic import BaseModel

from pico_ai.provider import Provider
from pico_ai.types import AICallRequest, Message, StreamEvent, ToolCall, Usage

from .session import (
    AssistantBlock,
    AssistantPayload,
    CompactionSummaryPayload,
    Node,
    Session,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)
from .tools import ToolRegistry


class AgentState(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    TOOL_EXECUTING = "tool_executing"
    COMPACTING = "compacting"
    DONE = "done"
    ERROR = "error"


class LoopEvent(BaseModel):
    """A single observable event emitted by the agent loop."""

    kind: Literal[
        "text", "thinking", "tool_call", "tool_request", "tool_result", "state", "usage"
    ]
    text: str = ""
    thinking: str = ""
    tool_call: ToolCall | None = None
    tool_request: ToolRequestPayload | None = None
    tool_result: ToolResultPayload | None = None
    state: AgentState | None = None
    usage: Usage | None = None


class RunResult(BaseModel):
    """The outcome of a completed run."""

    text: str
    state: AgentState
    session: Session
    error: str | None = None


class HookSink(Protocol):
    """Lifecycle hooks the loop can fire (implemented by the SDK)."""

    async def on_session_start(self, session: Session) -> None: ...
    async def tool_before(self, name: str, arguments: dict) -> None: ...
    async def tool_after(
        self, name: str, arguments: dict, result: ToolResultPayload
    ) -> None: ...


Summarizer = Callable[[list[Node], str], AsyncIterator[StreamEvent]]


async def _collect_text(stream: AsyncIterator[StreamEvent]) -> str:
    parts: list[str] = []
    async for event in stream:
        if event.kind == "text":
            parts.append(event.text)
    return "".join(parts)


class AgentLoop:
    """Drives a session through the FSM using a provider and a tool registry."""

    def __init__(
        self,
        provider: Provider,
        session: Session,
        tools: ToolRegistry,
        *,
        system_prompt: str = "",
        model: str = "",
        context_window: int = 128_000,
        reserve_tokens: int = 16_384,
        summarizer: Summarizer | None = None,
        hooks: HookSink | None = None,
    ) -> None:
        self.provider = provider
        self.session = session
        self.tools = tools
        self.system_prompt = system_prompt
        self.model = model
        self.context_window = context_window
        self.reserve_tokens = reserve_tokens
        self._summarizer = summarizer or self._default_summarizer
        self._hooks = hooks
        self.state = AgentState.IDLE
        self._started = False

    # -- public -------------------------------------------------------------

    async def run(self, prompt: str) -> RunResult:
        """Run the full loop and return the final result."""
        text_parts: list[str] = []
        try:
            async for event in self.stream(prompt):
                if event.kind == "text":
                    text_parts.append(event.text)
        except Exception as exc:  # noqa: BLE001 - surface as error state
            self.state = AgentState.ERROR
            return RunResult(
                text="".join(text_parts),
                state=AgentState.ERROR,
                session=self.session,
                error=str(exc),
            )
        return RunResult(
            text="".join(text_parts), state=self.state, session=self.session
        )

    async def stream(self, prompt: str) -> AsyncIterator[LoopEvent]:
        """Run the loop, yielding observable events as they happen."""
        self._set_state(AgentState.IDLE)
        yield self._state_event()
        if self._hooks is not None and not self._started:
            await self._hooks.on_session_start(self.session)
            self._started = True

        self.session.append(
            self.session.active_leaf_id, UserPayload(content=prompt)
        )

        while True:
            if self._needs_compaction():
                async for event in self._compact():
                    yield event

            self._set_state(AgentState.STREAMING)
            yield self._state_event()

            request = self._build_request()
            blocks: list[AssistantBlock] = []
            tool_calls: list[ToolCall] = []
            usage: Usage | None = None

            async for stream_event in self.provider.stream(request):
                if stream_event.kind == "text":
                    blocks.append(AssistantBlock(kind="text", text=stream_event.text))
                    yield LoopEvent(kind="text", text=stream_event.text)
                elif stream_event.kind == "thinking":
                    blocks.append(AssistantBlock(kind="thinking", thinking=stream_event.thinking))
                    yield LoopEvent(kind="thinking", thinking=stream_event.thinking)
                elif stream_event.kind == "tool_call" and stream_event.tool_call is not None:
                    tool_calls.append(stream_event.tool_call)
                    yield LoopEvent(kind="tool_call", tool_call=stream_event.tool_call)
                elif stream_event.kind == "usage" and stream_event.usage is not None:
                    usage = stream_event.usage

            self.session.append(
                self.session.active_leaf_id,
                AssistantPayload(blocks=blocks, usage=usage),
            )
            if usage is not None:
                yield LoopEvent(kind="usage", usage=usage)

            if not tool_calls:
                self._set_state(AgentState.DONE)
                yield self._state_event()
                return

            self._set_state(AgentState.TOOL_EXECUTING)
            yield self._state_event()

            for tool_call in tool_calls:
                request_payload = ToolRequestPayload(tool_call=tool_call)
                self.session.append(self.session.active_leaf_id, request_payload)
                yield LoopEvent(kind="tool_request", tool_request=request_payload)
                result = await self._execute_tool(tool_call)
                self.session.append(self.session.active_leaf_id, result)
                yield LoopEvent(kind="tool_result", tool_result=result)

    # -- context assembly ---------------------------------------------------

    def _build_request(self) -> AICallRequest:
        messages: list[Message] = []
        last_assistant = -1
        for node in self.session.active_branch():
            payload = node.payload
            if isinstance(payload, CompactionSummaryPayload):
                # A compaction summary replaces everything before it.
                messages = [
                    Message(role="user", content=f"[compacted context]\n{payload.summary}")
                ]
                last_assistant = -1
            elif isinstance(payload, UserPayload):
                messages.append(Message(role="user", content=payload.content))
                last_assistant = -1
            elif isinstance(payload, AssistantPayload):
                messages.append(Message(role="assistant", content=payload.text))
                last_assistant = len(messages) - 1
            elif isinstance(payload, ToolRequestPayload):
                if last_assistant >= 0:
                    messages[last_assistant].tool_calls.append(payload.tool_call)
            elif isinstance(payload, ToolResultPayload):
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=payload.tool_call_id,
                        name=payload.name,
                        content=payload.content,
                    )
                )
                # Note: do NOT reset last_assistant here — a single assistant
                # turn can make several tool calls, and every tool_request in
                # that turn must attach to the same assistant message.
        return AICallRequest(
            system=self.system_prompt,
            messages=messages,
            tools=self.tools.definitions(),
            model=self.model,
        )

    def _context_text(self) -> str:
        parts = [self.system_prompt]
        for node in self.session.active_branch():
            payload = node.payload
            if isinstance(payload, CompactionSummaryPayload):
                # A summary replaces everything before it.
                parts = [self.system_prompt, payload.summary]
            elif isinstance(payload, UserPayload):
                parts.append(payload.content)
            elif isinstance(payload, AssistantPayload):
                parts.append(payload.text)
            elif isinstance(payload, ToolResultPayload):
                parts.append(payload.content)
        return "\n".join(parts)

    def estimate_tokens(self) -> int:
        """Return the current estimated token count for the context."""
        return self._estimate_tokens()

    def _estimate_tokens(self) -> int:
        # Rough heuristic: ~4 characters per token.
        return len(self._context_text()) // 4

    # -- compaction ---------------------------------------------------------

    def _needs_compaction(self) -> bool:
        return self._estimate_tokens() > self.context_window - self.reserve_tokens

    def _split_for_compaction(
        self, branch: list[Node], keep_turns: int
    ) -> tuple[list[Node], list[Node]]:
        """Split the branch into (to_summarize, to_keep).

        The ``keep_turns`` most recent user turns — and everything after them —
        form the recent window; everything before them is summarised.
        """
        user_indices = [
            i for i, n in enumerate(branch) if isinstance(n.payload, UserPayload)
        ]
        if len(user_indices) <= keep_turns:
            return [], branch
        cutoff = user_indices[-keep_turns]
        return branch[:cutoff], branch[cutoff:]

    async def _compact(
        self, instructions: str = "", keep_turns: int = 1
    ) -> AsyncIterator[LoopEvent]:
        self._set_state(AgentState.COMPACTING)
        yield self._state_event()
        branch = self.session.active_branch()
        to_summarize, to_keep = self._split_for_compaction(branch, keep_turns)
        if not to_summarize:
            return
        summary = await _collect_text(self._summarizer(to_summarize, instructions))
        self.session.append(
            self.session.active_leaf_id, CompactionSummaryPayload(summary=summary)
        )
        # Re-append the kept recent window after the summary (append-only).
        for node in to_keep:
            self.session.append(self.session.active_leaf_id, node.payload)

    async def compact(self, instructions: str = "") -> None:
        """Manually trigger compaction with optional steering instructions."""
        async for _ in self._compact(instructions):
            pass

    async def _default_summarizer(
        self, nodes: list[Node], instructions: str
    ) -> AsyncIterator[StreamEvent]:
        content = "\n".join(_render_node(n) for n in nodes)
        request = AICallRequest(
            system="Summarize the following conversation, preserving all key facts, "
            "decisions, and outstanding tasks.",
            messages=[Message(role="user", content=content + "\n" + instructions)],
            model=self.model,
        )
        async for event in self.provider.stream(request):
            yield event

    # -- tool execution -----------------------------------------------------

    async def _execute_tool(self, tool_call: ToolCall) -> ToolResultPayload:
        tool = self.tools.get(tool_call.name)
        if tool is None:
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"error: unknown tool: {tool_call.name}",
                is_error=True,
            )
        if self._hooks is not None:
            await self._hooks.tool_before(tool_call.name, tool_call.arguments)
        try:
            outcome = await tool.run(tool_call.arguments)
        except Exception as exc:  # noqa: BLE001 - surface as a result
            result = ToolResultPayload(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"error: {exc}",
                is_error=True,
            )
        else:
            result = ToolResultPayload(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=outcome.content,
                is_error=outcome.is_error,
            )
        if self._hooks is not None:
            await self._hooks.tool_after(tool_call.name, tool_call.arguments, result)
        return result

    # -- helpers ------------------------------------------------------------

    def _set_state(self, state: AgentState) -> None:
        self.state = state

    def _state_event(self) -> LoopEvent:
        return LoopEvent(kind="state", state=self.state)


def _render_node(node: Node) -> str:
    payload = node.payload
    if isinstance(payload, UserPayload):
        return f"user: {payload.content}"
    if isinstance(payload, AssistantPayload):
        return f"assistant: {payload.text}"
    if isinstance(payload, ToolRequestPayload):
        return f"tool request: {payload.tool_call.name} {payload.tool_call.arguments}"
    if isinstance(payload, ToolResultPayload):
        return f"tool result: {payload.content}"
    if isinstance(payload, CompactionSummaryPayload):
        return f"summary: {payload.summary}"
    return ""
