"""Ticket 03 — trace view overlay: snapshot, filter, errors-only, refresh."""

from textual.app import App
from textual.widgets import Input, Label, OptionList

from pico_ai.types import StreamEvent
from pico_core.session import AssistantBlock, AssistantPayload, Session, UserPayload
from pico_core.trace import assemble_trace_rows
from pico_tui.commands import Command, parse_line
from pico_tui.trace_view import TraceViewScreen

from pico_tui.app import PicoApp, _SessionManager

from conftest import FakeProvider, make_session


def _session() -> Session:
    session = Session()
    root = session.append(None, UserPayload(content="do it"))
    session.append(
        root.id,
        AssistantPayload(blocks=[AssistantBlock(kind="text", text="working")]),
    )
    return session


def _rows(session: Session):
    return assemble_trace_rows(session.active_branch())


class Host(App[None]):
    def compose(self):
        yield Label("host")


async def _open(session: Session, app: Host, on_refresh=None):
    captured: list = []
    screen = TraceViewScreen(_rows(session), on_refresh=on_refresh)
    app.push_screen(screen, callback=captured.append)
    return screen, captured


def test_parse_line_trace():
    assert parse_line("/trace") == Command("trace")


async def test_overlay_shows_one_row_per_node():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_session(), app)
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 2


async def test_typing_filters_by_summary():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_session(), app)
        await pilot.pause()
        screen.query_one("#trace-search", Input).value = "working"
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 1
        assert screen._visible == [1]


def _error_session() -> Session:
    from pico_ai.types import ToolCall

    from pico_core.session import ToolRequestPayload, ToolResultPayload

    session = Session()
    root = session.append(None, UserPayload(content="run it"))
    req = session.append(
        root.id,
        ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="bash", arguments={"command": "exit 1"})
        ),
    )
    session.append(
        req.id,
        ToolResultPayload(
            tool_call_id="c1", name="bash", content="boom\n[exit code: 1]", is_error=True
        ),
    )
    return session


async def test_errors_only_toggle_hides_ok_rows():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_error_session(), app)
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 3
        screen.action_errors_only()
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 1
        screen.action_errors_only()
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 3


async def test_errors_only_key_via_list_focus():
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(_error_session(), app)
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 1


async def test_errors_only_toggle():
    from pico_ai.types import ToolCall

    from pico_core.session import ToolRequestPayload, ToolResultPayload

    session = Session()
    root = session.append(None, UserPayload(content="run it"))
    req = session.append(
        root.id,
        ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"})
        ),
    )
    session.append(
        req.id, ToolResultPayload(tool_call_id="c1", name="read", content="hello")
    )
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(session, app)
        await pilot.pause()
        screen.action_errors_only()
        await pilot.pause()
        assert screen._visible == []
        assert screen.query_one("#trace-list", OptionList).option_count == 1
        screen.action_errors_only()
        await pilot.pause()
        assert screen._visible == [0, 1, 2]
        assert screen.query_one("#trace-list", OptionList).option_count == 3


async def test_refresh_reloads_rows():
    session = _session()
    app = Host()
    async with app.run_test() as pilot:
        screen, _ = await _open(session, app, on_refresh=lambda: _rows(session))
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 2
        session.append(session.active_leaf_id, UserPayload(content="more"))
        screen.action_refresh()
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 3


async def test_empty_session_shows_no_nodes():
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(Session(), app)
        await pilot.pause()
        assert screen.query_one("#trace-list", OptionList).option_count == 1
        await pilot.press("escape")
        await pilot.pause()
    assert captured == [None]


async def test_enter_dismisses():
    app = Host()
    async with app.run_test() as pilot:
        screen, captured = await _open(_session(), app)
        await pilot.pause()
        screen.query_one("#trace-search", Input).value = "working"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert captured == [None]


async def _pico_app(tmp_path) -> PicoApp:
    core = _session()
    session = make_session(
        FakeProvider([[StreamEvent(kind="text", text="hi")]]), tmp_path, session=core
    )
    return PicoApp(_SessionManager(session))


async def test_ctrl_t_opens_trace_view(tmp_path):
    from pico_tui.trace_view import TraceViewScreen

    app = await _pico_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert isinstance(app.screen, TraceViewScreen)
        assert app.screen.query_one("#trace-list", OptionList).option_count == 2


async def test_slash_trace_opens_trace_view(tmp_path):
    from pico_tui.trace_view import TraceViewScreen

    app = await _pico_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        input_bar = app.query_one("#input-bar", Input)
        input_bar.value = "/trace"
        input_bar.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, TraceViewScreen)
