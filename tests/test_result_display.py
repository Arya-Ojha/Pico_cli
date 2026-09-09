"""Result display: hidden todo calls, titled todo results, collapsible bash."""

from rich.panel import Panel

from pico_ai.types import StreamEvent, ToolCall
from pico_core.fsm import LoopEvent
from pico_core.session import ToolRequestPayload, ToolResultPayload
from pico_tui.app import (
    BashResultSegment,
    PicoApp,
    _SessionManager,
    parse_bash_result,
)
from pico_tui.render import render_event

from conftest import FakeProvider, make_session


def _request(name: str, arguments: dict) -> LoopEvent:
    return LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name=name, arguments=arguments)
        ),
    )


def _result(name: str, content: str) -> LoopEvent:
    return LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(
            tool_call_id="c1", name=name, content=content
        ),
    )


# ── todo: call hidden, result titled ──────────────────────────────


def test_todo_tool_request_renders_nothing():
    assert (
        render_event(_request("todo", {"action": "list"})) is None
    )


def test_todo_tool_result_titled_todo():
    rendered = render_event(_result("todo", "added [t1] pending — x"))
    assert isinstance(rendered, Panel)
    assert "todo" in rendered.title
    assert "result" not in rendered.title


# ── read: call shown, result hidden ───────────────────────────────


def test_read_tool_request_renders_panel():
    rendered = render_event(_request("read", {"path": "a.txt"}))
    assert isinstance(rendered, Panel)
    assert rendered.border_style == "bright_blue"


def test_read_tool_result_renders_nothing():
    assert render_event(_result("read", "hello world from file")) is None


# ── bash parsing ──────────────────────────────────────────────────


def test_parse_bash_result_splits_exit_code():
    seg = parse_bash_result("hi\n[exit code: 0]")
    assert seg.body == "hi"
    assert seg.exit_code == 0
    assert seg.success is True


def test_parse_bash_result_error():
    seg = parse_bash_result("boom\n[exit code: 1]")
    assert seg.body == "boom"
    assert seg.exit_code == 1
    assert seg.success is False


def test_parse_bash_result_without_marker():
    seg = parse_bash_result("odd output")
    assert seg.body == "odd output"
    assert seg.exit_code is None
    assert seg.success is False


# ── bash rendering ────────────────────────────────────────────────


def _app() -> PicoApp:
    class _FakeSession:
        pass

    return PicoApp(_SessionManager(_FakeSession()))  # type: ignore[arg-type]


def test_bash_collapsed_success_and_error():
    app = _app()
    ok = app._bash_renderable(BashResultSegment(body="hi", exit_code=0, id=1))
    assert isinstance(ok, str)
    assert "success" in ok
    assert ok.startswith("[@click=app.toggle_bash(1)]")
    assert "hi" not in ok  # output stays hidden until clicked

    err = app._bash_renderable(BashResultSegment(body="boom", exit_code=1, id=2))
    assert isinstance(err, str)
    assert "error" in err
    assert err.startswith("[@click=app.toggle_bash(2)]")
    assert "boom" not in err


def test_bash_expanded_shows_full_body():
    app = _app()
    app._bash_expanded.add(3)
    rendered = app._bash_renderable(
        BashResultSegment(body="line1\nline2", exit_code=1, id=3)
    )
    assert isinstance(rendered, str)
    assert "line2" in rendered
    assert rendered.startswith("[@click=app.toggle_bash(3)]")


async def test_chat_log_has_no_link_styling(tmp_path):
    """No link colors or hover highlights on clickable lines in the chat."""
    from textual.widgets import RichLog

    session = make_session(FakeProvider([]), tmp_path)
    app = PicoApp(_SessionManager(session))
    async with app.run_test():
        assert app.query_one("#chat-log", RichLog).auto_links is False


# ── manager stream: what reaches the chat ─────────────────────────


async def _captured(tmp_path, turns) -> tuple[list, PicoApp]:
    session = make_session(FakeProvider(turns), tmp_path)
    mgr = _SessionManager(session)
    captured: list = []
    await mgr.stream("hi", captured.append)
    return captured, PicoApp(mgr)


async def test_manager_hides_todo_call_but_shows_result(tmp_path):
    captured, _ = await _captured(
        tmp_path,
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="todo",
                        arguments={"action": "add", "text": "x"},
                    ),
                )
            ],
            [StreamEvent(kind="text", text="done")],
        ],
    )
    panels = [c for c in captured if isinstance(c, Panel)]
    assert len(panels) == 1
    assert "todo" in panels[0].title
    assert "result" not in panels[0].title


async def test_manager_bash_yields_collapsible_segment(tmp_path):
    captured, _ = await _captured(
        tmp_path,
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1", name="bash", arguments={"command": "echo hi"}
                    ),
                )
            ],
            [StreamEvent(kind="text", text="done")],
        ],
    )
    segs = [c for c in captured if isinstance(c, BashResultSegment)]
    assert len(segs) == 1
    assert segs[0].success is True
    assert "hi" in segs[0].body
    # The `$ command` request echo stays; the result panel is gone.
    panels = [c for c in captured if isinstance(c, Panel)]
    assert len(panels) == 1
    assert "result" not in panels[0].title


async def test_bash_toggle_expands_in_live_app(tmp_path):
    session = make_session(
        FakeProvider(
            [
                [
                    StreamEvent(
                        kind="tool_call",
                        tool_call=ToolCall(
                            id="c1", name="bash", arguments={"command": "echo hi"}
                        ),
                    )
                ],
                [StreamEvent(kind="text", text="done")],
            ]
        ),
        tmp_path,
    )
    app = PicoApp(_SessionManager(session))
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "run it"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        segs = [
            t for t in app._transcript if isinstance(t, BashResultSegment)
        ]
        assert len(segs) == 1
        assert segs[0].id is not None
        await app.action_toggle_bash(segs[0].id)
        assert segs[0].id in app._bash_expanded
