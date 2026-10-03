"""Tickets 02/03/05 — the FSM loop: tracer bullet, tool loop, compaction."""

import pytest

from pico_ai.types import StreamEvent, ToolCall
from pico_core.fsm import AgentLoop, AgentState
from pico_core.session import (
    AssistantPayload,
    CompactionSummaryPayload,
    Session,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)
from pico_core.tools import ReadTool, ToolRegistry

from conftest import FakeProvider


def _registry(cwd):
    reg = ToolRegistry()
    reg.register(ReadTool(cwd))
    return reg


def _states(events):
    return [e.state for e in events if e.kind == "state"]


async def test_tracer_bullet_text_response():
    provider = FakeProvider([[StreamEvent(kind="text", text="Hello")]])
    session = Session()
    loop = AgentLoop(provider, session, ToolRegistry())
    result = await loop.run("hello")
    assert result.text == "Hello"
    assert result.state == AgentState.DONE
    branch = session.active_branch()
    assert [type(n.payload) for n in branch] == [UserPayload, AssistantPayload]
    assert branch[0].payload.content == "hello"
    assert branch[1].payload.text == "Hello"


async def test_assistant_node_carries_stream_duration():
    provider = FakeProvider([[StreamEvent(kind="text", text="Hello")]])
    session = Session()
    loop = AgentLoop(provider, session, ToolRegistry())
    await loop.run("hello")
    assistants = [n.payload for n in session.active_branch() if isinstance(n.payload, AssistantPayload)]
    assert len(assistants) == 1
    assert assistants[0].duration_ms is not None
    assert assistants[0].duration_ms >= 0


async def test_assistant_nodes_on_tool_turns_carry_duration(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = Session()
    loop = AgentLoop(provider, session, _registry(tmp_path))
    await loop.run("read a.txt")
    assistants = [n.payload for n in session.active_branch() if isinstance(n.payload, AssistantPayload)]
    assert len(assistants) == 2
    for payload in assistants:
        assert payload.duration_ms is not None
        assert payload.duration_ms >= 0


async def test_interrupted_stream_appends_partial_duration():
    async def _failing_stream(request):
        yield StreamEvent(kind="text", text="partial")
        raise RuntimeError("boom")

    class FailingProvider:
        async def stream(self, request):
            async for event in _failing_stream(request):
                yield event

    session = Session()
    loop = AgentLoop(FailingProvider(), session, ToolRegistry())
    with pytest.raises(RuntimeError, match="boom"):
        [e async for e in loop.stream("hi")]
    assistants = [n.payload for n in session.active_branch() if isinstance(n.payload, AssistantPayload)]
    assert len(assistants) == 1
    assert assistants[0].text == "partial"
    assert assistants[0].duration_ms is not None
    assert assistants[0].duration_ms >= 0


async def test_fsm_transitions_tracer_bullet():
    provider = FakeProvider([[StreamEvent(kind="text", text="Hi")]])
    loop = AgentLoop(provider, Session(), ToolRegistry())
    events = [e async for e in loop.stream("hi")]
    assert _states(events) == [
        AgentState.IDLE,
        AgentState.STREAMING,
        AgentState.DONE,
    ]


async def test_tool_loop_and_result_feedback(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = Session()
    loop = AgentLoop(provider, session, _registry(tmp_path))
    result = await loop.run("read a.txt")
    assert result.text == "done"
    assert result.state == AgentState.DONE
    kinds = [type(n.payload) for n in session.active_branch()]
    assert kinds == [
        UserPayload,
        AssistantPayload,
        ToolRequestPayload,
        ToolResultPayload,
        AssistantPayload,
    ]
    # tool result fed back into the second request
    second = provider.calls[1]
    tool_msgs = [m for m in second.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "hello"
    # first request carried the tool definition
    assert [t.name for t in provider.calls[0].tools] == ["read"]


async def test_fsm_transitions_tool_loop(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    loop = AgentLoop(provider, Session(), _registry(tmp_path))
    events = [e async for e in loop.stream("read a.txt")]
    assert _states(events) == [
        AgentState.IDLE,
        AgentState.STREAMING,
        AgentState.TOOL_EXECUTING,
        AgentState.STREAMING,
        AgentState.DONE,
    ]


async def test_tool_error_surfaces_as_result(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "missing.txt"}))],
            [StreamEvent(kind="text", text="recovered")],
        ]
    )
    session = Session()
    loop = AgentLoop(provider, session, _registry(tmp_path))
    result = await loop.run("read missing.txt")
    assert result.state == AgentState.DONE  # did not crash
    results = [n.payload for n in session.active_branch() if isinstance(n.payload, ToolResultPayload)]
    assert results[0].is_error
    assert "not found" in results[0].content


async def test_manual_compact_with_instructions():
    seen = {}

    async def fake_summarizer(nodes, instructions):
        seen["instructions"] = instructions
        yield StreamEvent(kind="text", text="SUMMARY")

    provider = FakeProvider(
        [
            [StreamEvent(kind="text", text="r1")],
            [StreamEvent(kind="text", text="r2")],
        ]
    )
    session = Session()
    loop = AgentLoop(provider, session, ToolRegistry(), summarizer=fake_summarizer)
    await loop.run("one")
    await loop.run("two")
    await loop.compact("focus on the bug")
    assert seen["instructions"] == "focus on the bug"
    kinds = [type(n.payload) for n in session.active_branch()]
    assert CompactionSummaryPayload in kinds


async def test_auto_compaction_keeps_recent_window():
    async def fake_summarizer(nodes, instructions):
        yield StreamEvent(kind="text", text="SUMMARY")

    provider = FakeProvider(
        [
            [StreamEvent(kind="text", text="r1")],
            [StreamEvent(kind="text", text="r2")],
        ]
    )
    session = Session()
    loop = AgentLoop(
        provider,
        session,
        ToolRegistry(),
        context_window=50,
        reserve_tokens=0,
        summarizer=fake_summarizer,
    )
    await loop.run("p1 " * 50)
    await loop.run("p2 " * 50)
    kinds = [type(n.payload) for n in session.active_branch()]
    assert CompactionSummaryPayload in kinds
    # the post-compaction request starts from the summary, not the old prompt
    last = provider.calls[-1]
    assert last.messages[0].role == "user"
    assert "SUMMARY" in last.messages[0].content
    assert "p1" not in last.messages[0].content
    # the recent window (p2) is kept in context
    assert any("p2" in m.content for m in last.messages)


async def test_multiple_tool_calls_in_one_turn(tmp_path):
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    provider = FakeProvider(
        [
            [
                StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"})),
                StreamEvent(kind="tool_call", tool_call=ToolCall(id="c2", name="read", arguments={"path": "b.txt"})),
            ],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = Session()
    loop = AgentLoop(provider, session, _registry(tmp_path))
    result = await loop.run("read both")
    assert result.state == AgentState.DONE

    second = provider.calls[1]
    tool_msgs = [m for m in second.messages if m.role == "tool"]
    assert len(tool_msgs) == 2
    assistants = [m for m in second.messages if m.role == "assistant" and m.tool_calls]
    assert len(assistants) == 1
    assert [tc.id for tc in assistants[0].tool_calls] == ["c1", "c2"]
    # every tool result's id is covered by the assistant's tool_calls
    ids = {tc.id for tc in assistants[0].tool_calls}
    assert all(tm.tool_call_id in ids for tm in tool_msgs)


async def test_bash_echo_ordering(tmp_path):
    from pico_core.tools import BashTool

    provider = FakeProvider(
        [
            [StreamEvent(kind="tool_call", tool_call=ToolCall(id="c1", name="bash", arguments={"command": "echo hi"}))],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    reg = ToolRegistry()
    reg.register(BashTool(tmp_path, enabled=True))
    loop = AgentLoop(provider, Session(), reg)
    events = [e async for e in loop.stream("run echo")]
    request_idx = next(i for i, e in enumerate(events) if e.kind == "tool_request")
    result_idx = next(i for i, e in enumerate(events) if e.kind == "tool_result")
    assert request_idx < result_idx
    assert events[request_idx].tool_request.tool_call.arguments["command"] == "echo hi"
