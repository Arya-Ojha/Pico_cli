"""Pointer cursor over clickable chat toggle lines (no hover restyle)."""

from rich.text import Text
from textual.widgets import RichLog

from pico_tui.app import PicoApp, ThinkingSegment, _SessionManager

from conftest import FakeProvider, make_session


async def _seed_log(app: PicoApp, pilot) -> None:
    """Leave one plain line + one collapsed toggle line in the chat log."""
    app._write_chat(Text("plain line"))
    app._transcript.append(
        ThinkingSegment(text="line one\nline two", id=1, final=True)
    )
    app._rerender_chat()
    await pilot.pause()


def _run(tmp_path) -> PicoApp:
    return PicoApp(_SessionManager(make_session(FakeProvider([]), tmp_path)))


async def test_pointer_over_toggle_line(tmp_path):
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await _seed_log(app, pilot)
        assert app.screen._pointer_shape == "default"

        # line 1 is the collapsed thinking toggle (full-width click target)
        await pilot.hover("#chat-log", offset=(2, 1))
        await pilot.pause()
        assert app.screen._pointer_shape == "pointer"


async def test_default_pointer_over_plain_line(tmp_path):
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await _seed_log(app, pilot)
        await pilot.hover("#chat-log", offset=(2, 1))
        await pilot.pause()
        assert app.screen._pointer_shape == "pointer"

        # line 0 is plain text — back to the default pointer
        await pilot.hover("#chat-log", offset=(2, 0))
        await pilot.pause()
        assert app.screen._pointer_shape == "default"


async def test_default_pointer_outside_chat_log(tmp_path):
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await _seed_log(app, pilot)
        await pilot.hover("#chat-log", offset=(2, 1))
        await pilot.pause()
        assert app.screen._pointer_shape == "pointer"

        # the input bar is a text field — I-beam pointer (Textual default)
        await pilot.hover("#input-bar")
        await pilot.pause()
        assert app.screen._pointer_shape == "text"


async def test_hovered_click_action_parses_toggle(tmp_path):
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await _seed_log(app, pilot)
        await pilot.hover("#chat-log", offset=(2, 1))
        await pilot.pause()
        action = app._hovered_click_action()
        assert action is not None
        assert action[0] == "app.toggle_thinking"

        await pilot.hover("#chat-log", offset=(2, 0))
        await pilot.pause()
        assert app._hovered_click_action() is None


async def test_click_still_toggles(tmp_path):
    """Pointer tracking must not break the click action itself."""
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await _seed_log(app, pilot)
        await pilot.hover("#chat-log", offset=(2, 1))
        await pilot.pause()
        await pilot.click("#chat-log", offset=(2, 1))
        await pilot.pause()
        await pilot.pause()
        assert 1 in app._thinking_expanded
        _ = app.query_one("#chat-log", RichLog)


def test_toggle_renderables_have_no_hint_or_hover_text(tmp_path):
    """Toggle lines are bare text: no hint labels, no hover styling."""
    from pico_tui.app import BashResultSegment

    app = _run(tmp_path)
    seg = ThinkingSegment(text="line one\nline two", id=1, final=True)
    collapsed = str(app._thinking_renderable(seg))
    for banned in ("show thinking", "hide", "underline"):
        assert banned not in collapsed

    bash_ok = BashResultSegment(body="out", exit_code=0, id=2)
    assert "show output" not in str(app._bash_renderable(bash_ok))

    bash_err = BashResultSegment(body="boom", exit_code=1, id=3)
    assert "show error" not in str(app._bash_renderable(bash_err))


async def test_chat_log_has_left_padding(tmp_path):
    """Chat content sits in from the left edge."""
    app = _run(tmp_path)
    async with app.run_test(size=(80, 24)):
        padding = app.query_one("#chat-log", RichLog).styles.padding
        assert padding.left >= 1
