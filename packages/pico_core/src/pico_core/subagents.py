"""Model-invoked sub-agents: the hardcoded ``task`` tool (see ADR-0005).

A parent loop delegates a self-contained chunk of work by calling ``task``
with a short description and a full prompt. A fresh child ``AgentLoop`` runs
on its own ``Session`` to completion; only the child's final summary returns
to the parent as the tool result, so the parent's context stays lean while
the child's full transcript persists as its own session file.

Children get a restricted toolset by default (read-only research tools),
fresh todos, and the parent's provider/model unless overridden per-spawn.
Recursion is bounded: ``task`` is only registered in a child when the
parent explicitly grants it, and ``max_depth`` caps the nesting — a spawn
requested beyond it returns an error result instead of running.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .fsm import AgentLoop, AgentState
from .tools import ToolOutcome


#: Default child toolset: read-only research tools. No writes, no bash, no
#: todos (child progress stays out of the parent's todo contract), and no
#: nested spawning unless the parent explicitly grants ``task``.
DEFAULT_CHILD_TOOLS = ["read", "grep", "fetch", "websearch"]

#: Maximum spawn nesting (the top-level loop runs at depth 0).
MAX_DEPTH = 2

#: Default per-child turn cap (streaming iterations of the child loop).
DEFAULT_MAX_TURNS = 25

#: Default per-child wall-clock budget in seconds.
DEFAULT_TIMEOUT_S = 300.0

#: Child result text beyond this is cut (with a note) before returning.
MAX_RESULT_CHARS = 8000


class ChildSpec(BaseModel):
    """What to run in the child loop."""

    description: str = ""
    prompt: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int = Field(default=DEFAULT_MAX_TURNS, ge=1)
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, gt=0)


class ChildFactory(Protocol):
    """Builds a child loop for ``spec`` at nesting ``depth``."""

    def __call__(self, spec: ChildSpec, depth: int) -> AgentLoop: ...


class SpawnTool:
    """Run a sub-agent to completion and return its summary."""

    name = "task"
    description = (
        "Delegate a self-contained task to a sub-agent. Give a short "
        "description and a full prompt; the sub-agent works autonomously "
        "with its own tools and context, and its final summary is returned. "
        "Use for parallelizable research or isolated chunks of work whose "
        "details the parent does not need."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
            },
            "model": {"type": "string"},
            "max_turns": {"type": "integer"},
            "timeout_s": {"type": "number"},
        },
        "required": ["prompt"],
    }

    def __init__(
        self,
        factory: ChildFactory,
        *,
        depth: int = 0,
        session_dir: Path | str = "~/.pico/sessions",
        default_tools: list[str] | None = None,
        max_depth: int = MAX_DEPTH,
        max_result_chars: int = MAX_RESULT_CHARS,
    ) -> None:
        self._factory = factory
        self._depth = depth
        self._session_dir = Path(session_dir).expanduser()
        self._default_tools = (
            list(default_tools) if default_tools is not None else list(DEFAULT_CHILD_TOOLS)
        )
        self._max_depth = max_depth
        self._max_result_chars = max_result_chars

    async def run(self, arguments: dict) -> ToolOutcome:
        prompt = (arguments.get("prompt") or "")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolOutcome(
                content="error: task requires a non-empty prompt", is_error=True
            )
        if self._depth >= self._max_depth:
            return ToolOutcome(
                content=(
                    f"error: sub-agent nesting limit reached "
                    f"(depth {self._depth} >= max {self._max_depth})"
                ),
                is_error=True,
            )
        spec_kwargs: dict[str, Any] = {
            "description": arguments.get("description", ""),
            "prompt": prompt.strip(),
        }
        for key in ("allowed_tools", "model", "max_turns", "timeout_s"):
            if arguments.get(key) is not None:
                spec_kwargs[key] = arguments[key]
        try:
            spec = ChildSpec.model_validate(spec_kwargs)
        except Exception as exc:  # noqa: BLE001 - invalid args are results
            return ToolOutcome(content=f"error: invalid task: {exc}", is_error=True)
        if spec.allowed_tools is None:
            spec = spec.model_copy(update={"allowed_tools": self._default_tools})
        try:
            loop = self._factory(spec, self._depth + 1)
        except Exception as exc:  # noqa: BLE001 - surface as a result
            return ToolOutcome(
                content=f"error: could not start sub-agent: {exc}", is_error=True
            )
        try:
            result = await asyncio.wait_for(
                loop.run(prompt.strip(), max_turns=spec.max_turns),
                timeout=spec.timeout_s,
            )
        except asyncio.TimeoutError:
            return ToolOutcome(
                content=(
                    f"[subagent {loop.session.id} timed out after "
                    f"{spec.timeout_s:g}s]"
                ),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a result
            return ToolOutcome(
                content=f"[subagent {loop.session.id} crashed: {exc}]",
                is_error=True,
            )
        # Best-effort persistence: the child's transcript lives as its own
        # session file; a save failure must not fail the delegation itself.
        try:
            loop.session.save(self._session_dir / f"{loop.session.id}.jsonl")
        except OSError:
            pass
        notes = []
        if result.truncated:
            notes.append(f"truncated at max_turns={spec.max_turns}")
        header = f"[subagent {loop.session.id} done"
        if notes:
            header += f" ({'; '.join(notes)})"
        header += "]"
        content = f"{header}\n{result.text}" if result.text else header
        if result.state == AgentState.ERROR:
            content = f"{content}\n[subagent ended in error: {result.error}]"
            return ToolOutcome(content=self._cut(content), is_error=True)
        return ToolOutcome(content=self._cut(content))

    def _cut(self, content: str) -> str:
        if len(content) <= self._max_result_chars:
            return content
        cut = len(content) - self._max_result_chars
        return content[: self._max_result_chars] + f"\n[... truncated {cut} chars ...]"
