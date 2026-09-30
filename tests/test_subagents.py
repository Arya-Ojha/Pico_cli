"""Sub-agents: the hardcoded ``task`` tool (ADR-0005).

Driven through the public ``AgentSession`` API with scripted providers —
the same seams as the rest of the suite. A prompt-routed provider keeps
parallel-child tests deterministic regardless of scheduling order.
"""

import asyncio

import pytest

from pico_ai.types import AICallRequest, StreamEvent, ToolCall
from pico_core.fsm import AgentLoop
from pico_core.session import Session
from pico_core.subagents import DEFAULT_CHILD_TOOLS, ChildSpec, SpawnTool
from pico_core.tools import ToolRegistry
from pico_sdk.config import Settings

from conftest import FakeProvider, make_session


def _task_call(call_id: str, prompt: str, **kwargs) -> StreamEvent:
    return StreamEvent(
        kind="tool_call",
        tool_call=ToolCall(
            id=call_id,
            name="task",
            arguments={"prompt": prompt, **kwargs},
        ),
    )


def _text(text: str) -> StreamEvent:
    return StreamEvent(kind="text", text=text)


class RoutedProvider:
    """Serve scripted turns keyed by the latest user message (deterministic)."""

    def __init__(self, routes: dict[str, list[list[StreamEvent]]]) -> None:
        self.routes = {k: [list(t) for t in v] for k, v in routes.items()}
        self.calls: list[AICallRequest] = []

    async def stream(self, request: AICallRequest):
        self.calls.append(request)
        # Route on the FIRST user message: it is the run prompt and stays
        # stable across streaming passes (the last message changes as tool
        # results append, so it cannot key continuations).
        prompt = request.messages[0].content if request.messages else ""
        turns = self.routes.get(prompt, [])
        for event in turns.pop(0) if turns else []:
            yield event


# ── basic delegation ────────────────────────────────────────────────


async def test_spawn_returns_child_summary(tmp_path):
    # Turn order follows execution order: parent delegates, the child runs
    # to completion, then the parent continues.
    provider = FakeProvider(
        [
            [_task_call("t1", "research X", description="research")],
            [_text("child summary here")],
            [_text("parent done")],
        ]
    )
    session = make_session(provider, tmp_path)
    result = await session.run("go")
    assert result.text == "parent done"
    tool_results = [
        n.payload
        for n in session.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert len(tool_results) == 1
    assert "child summary here" in tool_results[0].content
    assert "subagent" in tool_results[0].content
    assert tool_results[0].is_error is False


async def test_spawn_sdk_method(tmp_path):
    provider = FakeProvider([[_text("scouted")]])
    session = make_session(provider, tmp_path)
    outcome = await session.spawn("scout the repo", description="scout")
    assert outcome.is_error is False
    assert "scouted" in outcome.content


async def test_spawn_empty_prompt_errors(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    outcome = await session.spawn("   ")
    assert outcome.is_error is True
    assert "non-empty prompt" in outcome.content


async def test_spawn_invalid_args_error(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    outcome = await session.spawn("x", max_turns=0)
    assert outcome.is_error is True


async def test_task_in_tool_definitions(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    names = [d.name for d in session.tools.definitions()]
    assert "task" in names


# ── child isolation ─────────────────────────────────────────────────


async def test_child_session_persisted_separately(tmp_path):
    provider = FakeProvider(
        [
            [_task_call("t1", "research X")],
            [_text("child summary here")],
            [_text("parent done")],
        ]
    )
    session = make_session(provider, tmp_path)
    await session.run("go")
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1  # only the child's; the parent was never saved
    content = files[0].read_text(encoding="utf-8")
    assert "child summary here" in content
    assert "parent done" not in content


async def test_parent_tree_holds_only_summary(tmp_path):
    provider = FakeProvider(
        [
            [_task_call("t1", "research X")],
            [_text("child inner detail")],
            [_text("parent done")],
        ]
    )
    session = make_session(provider, tmp_path)
    await session.run("go")
    texts = [
        n.payload.content
        for n in session.session.active_branch()
        if n.payload.kind == "user"
    ]
    assert texts == ["go"]  # the child prompt never enters the parent tree


def test_child_gets_restricted_tools_by_default(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    child = session._make_child_loop(ChildSpec(prompt="x"), 1)
    assert child.allowed_tools == DEFAULT_CHILD_TOOLS
    assert set(child.tools.names()) == {
        "read",
        "write",
        "edit",
        "grep",
        "fetch",
        "websearch",
        "bash",
        "todo",
    }
    assert "task" not in child.tools.names()


async def test_child_cannot_write_by_default(tmp_path):
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1", name="write", arguments={"path": "x.txt"}
                    ),
                )
            ],
            [_text("tried")],
        ]
    )
    session = make_session(provider, tmp_path)
    child = session._make_child_loop(ChildSpec(prompt="write it"), 1)
    result = await child.run("write it")
    assert "not allowed" in result.text or "tried" in result.text
    tool_results = [
        n.payload
        for n in child.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert tool_results[0].is_error is True
    assert "not allowed" in tool_results[0].content


async def test_per_spawn_allowlist_and_parent_intersection(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    child = session._make_child_loop(
        ChildSpec(prompt="x", allowed_tools=["read", "write"]), 1
    )
    assert child.allowed_tools == ["read", "write"]

    # The parent gate wins: a spawn cannot escalate past it.
    gated = Settings(session_dir=str(tmp_path), allowed_tools=["read"])
    gated_session = make_session(FakeProvider([]), tmp_path, settings=gated)
    gated_child = gated_session._make_child_loop(
        ChildSpec(prompt="x", allowed_tools=["read", "write"]), 1
    )
    assert gated_child.allowed_tools == ["read"]


async def test_parent_gate_can_deny_task(tmp_path):
    provider = FakeProvider(
        [
            [_task_call("t1", "research X")],
            [_text("done")],
        ]
    )
    settings = Settings(session_dir=str(tmp_path), allowed_tools=["read"])
    session = make_session(provider, tmp_path, settings=settings)
    result = await session.run("go")
    assert result.text == "done"
    tool_results = [
        n.payload
        for n in session.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert tool_results[0].is_error is True
    assert "not allowed" in tool_results[0].content


# ── recursion bound ─────────────────────────────────────────────────


async def test_nesting_limit_errors_instead_of_recursing(tmp_path):
    # Execution order: parent delegates, child delegates, grandchild hits
    # the depth cap (error result, no provider call), then each level
    # finishes in turn.
    provider = FakeProvider(
        [
            [_task_call("t1", "level 1", allowed_tools=["task"])],
            [_task_call("t2", "level 2", allowed_tools=["task"])],
            [_task_call("t3", "level 3")],
            [_text("grandchild done")],
            [_text("child done")],
            [_text("parent done")],
        ]
    )
    session = make_session(provider, tmp_path)
    result = await session.run("go")
    assert result.state.value == "done"
    # The error surfaces in the grandchild's persisted transcript (the
    # scripted finals don't echo it upward — only summaries propagate).
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 2  # child + grandchild; the parent was never saved
    assert any("nesting limit" in f.read_text(encoding="utf-8") for f in files)


# ── caps and crashes (SpawnTool unit level) ─────────────────────────


def _direct_loop(provider, turns_text: str = "") -> AgentLoop:
    return AgentLoop(provider=provider, session=Session(), tools=ToolRegistry())


async def test_max_turns_truncates(tmp_path):
    # max_turns counts streaming passes: two tool-call turns with a cap of
    # one truncates on the second pass (empty registry → error results).
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(id="c1", name="nope", arguments={}),
                )
            ],
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(id="c2", name="nope", arguments={}),
                )
            ],
        ]
    )
    loop = _direct_loop(provider)
    result = await loop.run("hi", max_turns=1)
    assert result.truncated is True
    assert result.state.value == "done"


async def test_max_turns_none_runs_to_done(tmp_path):
    provider = FakeProvider([[_text("hi")]])
    loop = _direct_loop(provider)
    result = await loop.run("hi")
    assert result.truncated is False
    assert result.text == "hi"


async def test_spawn_timeout_errors(tmp_path):
    class HangingProvider:
        async def stream(self, request):
            await asyncio.sleep(3600)
            yield _text("never")

    tool = SpawnTool(
        factory=lambda spec, depth: _direct_loop(HangingProvider()),
        session_dir=str(tmp_path),
    )
    outcome = await tool.run({"prompt": "slow", "timeout_s": 0.05})
    assert outcome.is_error is True
    assert "timed out" in outcome.content


async def test_spawn_child_crash_is_error_result(tmp_path):
    class ExplodingProvider:
        async def stream(self, request):
            raise RuntimeError("boom")
            yield _text("never")  # noqa: unreachable — keeps it a generator

    tool = SpawnTool(
        factory=lambda spec, depth: _direct_loop(ExplodingProvider()),
        session_dir=str(tmp_path),
    )
    outcome = await tool.run({"prompt": "fragile"})
    assert outcome.is_error is True
    assert "ended in error" in outcome.content


# ── parallelism ─────────────────────────────────────────────────────


async def test_parallel_tasks_complete_in_order(tmp_path):
    provider = RoutedProvider(
        {
            "go": [
                [
                    _task_call("t1", "job-a", description="first"),
                    _task_call("t2", "job-b", description="second"),
                ],
                [_text("parent done")],
            ],
            "job-a": [[_text("alpha")]],
            "job-b": [[_text("beta")]],
        }
    )
    session = make_session(provider, tmp_path)
    result = await session.run("go")
    assert result.text == "parent done"
    tool_results = [
        n.payload
        for n in session.session.active_branch()
        if n.payload.kind == "tool_result"
    ]
    assert len(tool_results) == 2
    assert "alpha" in tool_results[0].content
    assert "beta" in tool_results[1].content


async def test_mixed_turn_stays_sequential(tmp_path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(id="c1", name="read", arguments={"path": "note.txt"}),
                ),
                _task_call("t1", "research X"),
            ],
            [_text("child summary")],
            [_text("parent done")],
        ]
    )
    session = make_session(provider, tmp_path)
    kinds = [e.kind async for e in session.stream("go")]
    # sequential path: request/result pairs stay interleaved per call
    req_res = [k for k in kinds if k in ("tool_request", "tool_result")]
    assert req_res == ["tool_request", "tool_result", "tool_request", "tool_result"]


async def test_all_task_turn_groups_requests_first(tmp_path):
    provider = RoutedProvider(
        {
            "go": [
                [
                    _task_call("t1", "job-a"),
                    _task_call("t2", "job-b"),
                ],
                [_text("parent done")],
            ],
            "job-a": [[_text("alpha")]],
            "job-b": [[_text("beta")]],
        }
    )
    session = make_session(provider, tmp_path)
    kinds = [e.kind async for e in session.stream("go")]
    req_res = [k for k in kinds if k in ("tool_request", "tool_result")]
    assert req_res == ["tool_request", "tool_request", "tool_result", "tool_result"]
