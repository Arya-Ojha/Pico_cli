"""Ticket 02 — trace row assembly: node list -> trace rows (pure, no TUI)."""

import re

from pico_ai.types import ToolCall, Usage
from pico_core.session import (
    AssistantBlock,
    AssistantPayload,
    Node,
    Session,
    ToolRequestPayload,
    ToolResultPayload,
    UserPayload,
)
from pico_core.trace import assemble_trace_rows


def _assistant(text="reply", *, total=100, duration_ms=12.5):
    return AssistantPayload(
        blocks=[AssistantBlock(kind="text", text=text)],
        usage=Usage(input_tokens=80, output_tokens=20, total_tokens=total),
        duration_ms=duration_ms,
    )


def test_rows_cover_every_node_on_branch():
    session = Session()
    root = session.append(None, UserPayload(content="do it"))
    a = session.append(root.id, _assistant())
    req = session.append(
        a.id,
        ToolRequestPayload(tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"})),
    )
    res = session.append(
        req.id, ToolResultPayload(tool_call_id="c1", name="read", content="hello")
    )
    tail = session.append(res.id, _assistant("done"))
    rows = assemble_trace_rows(session.active_branch())
    assert [r.node_id for r in rows] == [root.id, a.id, req.id, res.id, tail.id]
    assert [r.kind for r in rows] == ["user", "assistant", "tool_request", "tool_result", "assistant"]
    for row in rows:
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", row.time), row.time


def test_summary_truncates_to_one_line():
    session = Session()
    session.append(None, UserPayload(content="line one\nline two " + "x" * 200))
    (row,) = assemble_trace_rows(session.active_branch())
    assert "\n" not in row.summary
    assert len(row.summary) <= 83
    assert row.summary.endswith("...")


def test_tool_duration_pairs_request_and_result():
    session = Session()
    req = session.append(
        None,
        ToolRequestPayload(tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"})),
    )
    session.append(
        req.id, ToolResultPayload(tool_call_id="c1", name="read", content="hello")
    )
    _, res = assemble_trace_rows(session.active_branch())
    assert res.duration_ms is not None
    assert res.duration_ms >= 0


def test_orphan_tool_result_shows_blank_duration():
    session = Session()
    session.append(
        None, ToolResultPayload(tool_call_id="ghost", name="read", content="hello")
    )
    (row,) = assemble_trace_rows(session.active_branch())
    assert row.duration_ms is None


def test_status_derives_from_is_error_and_bash_exit():
    session = Session()
    ok = session.append(
        None, ToolResultPayload(tool_call_id="c1", name="read", content="hello")
    )
    bad = session.append(
        ok.id,
        ToolResultPayload(
            tool_call_id="c2", name="bash", content="oops\n[exit code: 1]", is_error=True
        ),
    )
    rows = assemble_trace_rows(session.active_branch())
    assert rows[0].status == "ok"
    assert rows[1].status.startswith("error")
    assert "exit 1" in rows[1].status


def test_blank_tokens_and_durations_where_unknown():
    session = Session()
    root = session.append(None, UserPayload(content="hi"))
    session.append(
        root.id,
        AssistantPayload(blocks=[AssistantBlock(kind="text", text="hello")]),
    )
    user_row, asst_row = assemble_trace_rows(session.active_branch())
    assert user_row.tokens is None
    assert user_row.duration_ms is None
    assert user_row.status == "ok"
    assert asst_row.tokens is None
    assert asst_row.duration_ms is None


def test_assistant_row_carries_usage_total_and_duration():
    session = Session()
    session.append(None, _assistant(total=150, duration_ms=42.0))
    (row,) = assemble_trace_rows(session.active_branch())
    assert row.tokens == 150
    assert row.duration_ms == 42.0


def test_unparsable_timestamp_never_crashes():
    node = Node(payload=UserPayload(content="hi"), timestamp="not-a-time")
    (row,) = assemble_trace_rows([node])
    assert row.node_id == node.id
    assert row.kind == "user"
