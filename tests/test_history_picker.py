"""Interactive history picker: entries, screen dismissal, and fork-on-pick."""

from typing import Optional

import pytest
from textual.app import App
from textual.widgets import Label, OptionList

from pico_ai.types import StreamEvent
from pico_tui.app import PicoApp, _SessionManager
from pico_tui.history_picker import HistoryPickerScreen, format_history_option

from conftest import FakeProvider, make_session


def _entries(n: int = 3) -> list[dict]:
    return [
        {
            "index": i,
            "node_id": f"n{i}",
            "kind": "user" if i % 2 == 0 else "assistant",
            "summary": f"msg {i}",
            "is_current": i == n - 1,
        }
        for i in range(n)
    ]


# ── formatting ────────────────────────────────────────────────────


def test_format_history_option_plain():
    text = format_history_option(2, "user", "hello world")
    assert "2" in text
    assert "user" in text
    assert "hello world" in text
    assert "current" not in text


def test_format_history_option_marks_current():
    text = format_history_option(4, "assistant", "done", is_current=True)
    assert "current" in text


# ── entries ───────────────────────────────────────────────────────


async def test_history_entries_mirror_branch(tmp_path):
    provider = FakeProvider(
        [
            [StreamEvent(kind="text", text="first")],
            [StreamEvent(kind="text", text="second")],
        ]
    )
    session = make_session(provider, tmp_path)
    await session.run("one")
    await session.run("two")

    entries = _SessionManager(session).history_entries()
    assert [e["index"] for e in entries] == [0, 1, 2, 3]
    assert [e["kind"] for e in entries] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert entries[0]["summary"] == "one"
    assert entries[-1]["is_current"] is True
    assert all(e["is_current"] is False for e in entries[:-1])


def test_history_entries_empty_session(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    assert _SessionManager(session).history_entries() == []


# ── screen ────────────────────────────────────────────────────────


async def test_picker_dismisses_with_branch_index():
    """Selecting an option dismisses with its branch index, not the row."""

    class Host(App[Optional[int]]):
        def compose(self):
            yield Label("host")

    app = Host()
    async with app.run_test() as pilot:
        captured: list[int | None] = []
        screen = HistoryPickerScreen(_entries(3))
        app.push_screen(screen, callback=captured.append)
        await pilot.pause()

        option_list = screen.query_one("#history-picker-list", OptionList)
        option_list.highlighted = 0
        option_list.action_select()  # simulates Enter / click selection
        await pilot.pause()

    assert captured == [0]


async def test_picker_cancel_dismisses_with_none():
    class Host(App[Optional[int]]):
        def compose(self):
            yield Label("host")

    app = Host()
    async with app.run_test() as pilot:
        captured: list[int | None] = []
        app.push_screen(HistoryPickerScreen(_entries(3)), callback=captured.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert captured == [None]


# ── end-to-end: /history → pick → fork ────────────────────────────


def _make_app(tmp_path) -> tuple[PicoApp, FakeProvider]:
    provider = FakeProvider(
        [
            [StreamEvent(kind="text", text="first")],
            [StreamEvent(kind="text", text="second")],
        ]
    )
    session = make_session(provider, tmp_path)
    return PicoApp(_SessionManager(session)), provider


async def test_history_pick_forks_session(tmp_path):
    app, _ = _make_app(tmp_path)
    session = app._mgr.session
    await session.run("one")
    await session.run("two")
    branch = session.session.active_branch()
    assert len(branch) == 4

    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/history"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        option_list = app.screen.query_one("#history-picker-list", OptionList)
        option_list.highlighted = 1
        option_list.action_select()
        await pilot.pause()

        assert session.session.active_leaf_id == branch[1].id


async def test_history_cancel_keeps_session(tmp_path):
    app, _ = _make_app(tmp_path)
    session = app._mgr.session
    await session.run("one")
    leaf_before = session.session.active_leaf_id

    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/history"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert app.screen.query_one("#history-picker-list", OptionList) is not None
        await pilot.press("escape")
        await pilot.pause()

        assert session.session.active_leaf_id == leaf_before


async def test_history_empty_session_shows_notice(tmp_path):
    app, _ = _make_app(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#input-bar").value = "/history"
        await pilot.press("enter")
        await pilot.pause()

        # No picker for an empty session — a notice goes to the chat log.
        with pytest.raises(Exception):
            app.screen.query_one("#history-picker-list", OptionList)
        assert any(
            "empty session" in str(item) for item in app._transcript
        )
