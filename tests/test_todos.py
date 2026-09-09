"""Todo tool (tool seam) + side-panel behavior.

Prior art: tests/test_tools.py (plain async tool tests against tmp_path),
tests/test_history_picker.py (Textual pilot tests driving the real app).
"""

from pico_ai.types import StreamEvent, ToolCall
from pico_core.todos import TodoList, TodoTool, format_todos
from pico_tui.app import PicoApp, _SessionManager
from pico_tui.todo_panel import TodoPanel

from conftest import FakeProvider, make_session


# ── store ─────────────────────────────────────────────────────────


def test_store_add_assigns_ids_in_order():
    todos = TodoList()
    first = todos.add("one")
    second = todos.add("two")
    assert (first.id, first.status) == ("t1", "pending")
    assert second.id == "t2"
    assert len(todos) == 2


def test_store_update_status_and_text():
    todos = TodoList()
    item = todos.add("old")
    assert todos.update(item.id, status="in_progress") is not None
    assert todos.get(item.id).status == "in_progress"
    todos.update(item.id, text="new")
    assert todos.get(item.id).text == "new"


def test_store_update_unknown_id_returns_none():
    assert TodoList().update("t99", status="completed") is None


def test_store_clear_completed_only():
    todos = TodoList()
    todos.add("keep")
    done = todos.add("drop")
    todos.update(done.id, status="completed")
    assert todos.clear_completed() == 1
    assert [i.text for i in todos.all()] == ["keep"]


# ── tool ──────────────────────────────────────────────────────────


async def test_todo_add_and_list():
    tool = TodoTool(TodoList())
    out = await tool.run({"action": "add", "text": "write tests"})
    assert not out.is_error
    assert "t1" in out.content
    out = await tool.run({"action": "list"})
    assert "write tests" in out.content
    assert "pending" in out.content


async def test_todo_add_needs_text():
    out = await TodoTool(TodoList()).run({"action": "add", "text": "  "})
    assert out.is_error


async def test_todo_update_status():
    tool = TodoTool(TodoList())
    await tool.run({"action": "add", "text": "x"})
    out = await tool.run({"action": "update", "id": "t1", "status": "completed"})
    assert not out.is_error
    assert "completed" in out.content


async def test_todo_update_rejects_bad_status_and_unknown_id():
    tool = TodoTool(TodoList())
    out = await tool.run({"action": "update", "id": "t1", "status": "done"})
    assert out.is_error
    out = await tool.run({"action": "update", "id": "t99", "status": "completed"})
    assert out.is_error
    out = await tool.run({"action": "update", "id": "t1"})
    assert out.is_error


async def test_todo_clear_and_unknown_action():
    tool = TodoTool(TodoList())
    await tool.run({"action": "add", "text": "x"})
    await tool.run({"action": "update", "id": "t1", "status": "completed"})
    out = await tool.run({"action": "clear"})
    assert "1" in out.content
    assert format_todos(tool.todos.all()) == "(no todos)"
    out = await tool.run({"action": "explode"})
    assert out.is_error


async def test_todo_tool_registered_in_session(tmp_path):
    session = make_session(FakeProvider([]), tmp_path)
    assert "todo" in session.tools.names()


async def test_todo_tool_runs_through_agent_loop(tmp_path):
    """The agent can drive todos end-to-end via tool calls."""
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="todo",
                        arguments={"action": "add", "text": "write code"},
                    ),
                )
            ],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = make_session(provider, tmp_path)
    result = await session.run("do the thing")
    assert result.text == "done"
    assert [i.text for i in session.todos.all()] == ["write code"]


# ── run-until-done semantics ────────────────────────────────────


def _todo_call(call_id: str, arguments: dict) -> StreamEvent:
    return StreamEvent(
        kind="tool_call",
        tool_call=ToolCall(id=call_id, name="todo", arguments=arguments),
    )


async def test_run_continues_until_todos_completed(tmp_path):
    """Stopping early with open todos nudges the loop back in.

    Simulates the calculator flow: the model adds a todo, tries to stop,
    gets nudged, completes the todo, and only then does the run end.
    """
    provider = FakeProvider(
        [
            [_todo_call("c1", {"action": "add", "text": "write add()"})],
            [StreamEvent(kind="text", text="wrote it, stopping early")],
            [
                _todo_call(
                    "c2",
                    {"action": "update", "id": "t1", "status": "completed"},
                )
            ],
            [StreamEvent(kind="text", text="all done")],
        ]
    )
    session = make_session(provider, tmp_path)
    result = await session.run("make a calculator")

    assert result.text == "wrote it, stopping earlyall done"
    # Everything finished → the list is cleared for the next run.
    assert session.todos.all() == []
    assert len(provider.calls) == 4
    nudges = [
        n
        for n in session.session.active_branch()
        if n.payload.kind == "user" and "[todos]" in n.payload.content
    ]
    assert len(nudges) == 1


async def test_run_without_todos_ends_immediately(tmp_path):
    provider = FakeProvider([[StreamEvent(kind="text", text="hi")]])
    session = make_session(provider, tmp_path)
    result = await session.run("hello")
    assert result.text == "hi"
    assert len(provider.calls) == 1


async def test_stuck_run_keeps_unfinished_todos(tmp_path):
    """Todos left open by the nudge cap are kept, not cleared."""
    from pico_core.fsm import MAX_TODO_NUDGES

    provider = FakeProvider(
        [[StreamEvent(kind="text", text="almost")] for _ in range(20)]
    )
    session = make_session(provider, tmp_path)
    session.todos.add("stuck task")
    await session.run("do it")

    assert len(provider.calls) == 1 + MAX_TODO_NUDGES
    assert [i.text for i in session.todos.all()] == ["stuck task"]


async def test_todo_nudge_cap_terminates_stuck_run(tmp_path):
    """A model that never finishes its todos still terminates (no hang)."""
    from pico_core.fsm import MAX_TODO_NUDGES

    provider = FakeProvider(
        [[StreamEvent(kind="text", text="almost")] for _ in range(20)]
    )
    session = make_session(provider, tmp_path)
    session.todos.add("stuck task")
    result = await session.run("do it")

    assert len(provider.calls) == 1 + MAX_TODO_NUDGES
    assert result.state.value == "done"


# ── panel ─────────────────────────────────────────────────────────

async def test_panel_hidden_when_empty_and_shown_with_items(tmp_path):
    """Panel visibility flips on content (driven inside a live app)."""
    from pico_core.todos import TodoItem

    session = make_session(FakeProvider([]), tmp_path)
    app = PicoApp(_SessionManager(session))
    async with app.run_test():
        panel = app.query_one("#todo-panel", TodoPanel)
        assert panel.display is False
        panel.update_todos([TodoItem(id="t1", text="write code")])
        assert panel.display is True
        panel.update_todos([])
        assert panel.display is False


async def test_panel_appears_after_agent_adds_todo(tmp_path):
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    kind="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="todo",
                        arguments={"action": "add", "text": "write code"},
                    ),
                )
            ],
            [StreamEvent(kind="text", text="done")],
        ]
    )
    session = make_session(provider, tmp_path)
    app = PicoApp(_SessionManager(session))
    async with app.run_test() as pilot:
        panel = app.query_one("#todo-panel", TodoPanel)
        assert panel.display is False

        app.query_one("#input-bar").value = "do the thing"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert panel.display is True
