"""pico_tui tests: command parsing, Rich rendering, and Textual pilot."""

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from pico_ai.types import StreamEvent, ToolCall, Usage
from pico_core.fsm import LoopEvent
from pico_core.session import ToolRequestPayload, ToolResultPayload

from pico_tui.app import _SessionManager
from pico_tui.commands import Command, Prompt, parse_line
from pico_tui.render import render_event

# Shared console for rendering-to-text assertions.
_console = Console(force_terminal=True, color_system=None, width=200, height=100)


def _render_text(event: LoopEvent) -> str:
    """Render a LoopEvent to plain text for assertion."""
    rendered = render_event(event)
    if rendered is None:
        return ""
    with _console.capture() as capture:
        _console.print(rendered)
    return capture.get().rstrip()


# ── thinking collapse / expand ──────────────────────────────────────


async def test_rerender_preserves_scroll_position(tmp_path):
    """Expanding a block while scrolled up must not jump to the bottom."""
    from textual.widgets import RichLog

    from pico_tui.app import PicoApp, ThinkingSegment, _SessionManager

    from conftest import FakeProvider, make_session

    app = PicoApp(_SessionManager(make_session(FakeProvider([]), tmp_path)))
    async with app.run_test(size=(80, 24)) as pilot:
        for i in range(40):
            app._write_chat(Text(f"line {i}"))
        await pilot.pause()
        chat = app.query_one("#chat-log", RichLog)
        chat.scroll_to(y=0, animate=False)
        await pilot.pause()
        await pilot.pause()  # scroll_to lands on the second tick
        assert chat.scroll_y == 0

        app._transcript.append(
            ThinkingSegment(text="line one\nline two", id=1, final=True)
        )
        app._rerender_chat()
        await pilot.pause()
        await pilot.pause()
        assert chat.scroll_y == 0

        await app.action_toggle_thinking(1)  # expand in place
        await pilot.pause()
        await pilot.pause()
        assert chat.scroll_y == 0


def test_thinking_preview_single_line():
    from pico_tui.app import thinking_preview
    assert thinking_preview("hmm") == ("hmm", False)


def test_thinking_preview_multiline():
    from pico_tui.app import thinking_preview
    preview, truncated = thinking_preview("first line\nsecond line")
    assert preview == "first line"
    assert truncated is True


def test_thinking_renderable_collapsed_and_expanded():
    from pico_tui.app import PicoApp, ThinkingSegment

    class _FakeSession:
        pass

    app = PicoApp(_SessionManager(_FakeSession()))  # type: ignore[arg-type]
    seg = ThinkingSegment(text="line one\nline two", id=1, final=True)
    collapsed = app._thinking_renderable(seg)
    assert isinstance(collapsed, str)
    assert "💭 thinking: line one" in collapsed
    assert "…" in collapsed
    assert collapsed.startswith("[@click=app.toggle_thinking(1)]")
    # Expanding shows the full text instead, still clickable anywhere.
    app._thinking_expanded.add(1)
    expanded = app._thinking_renderable(seg)
    assert isinstance(expanded, str)
    assert "line two" in expanded
    assert expanded.startswith("[@click=app.toggle_thinking(1)]")


# ── parse_line ──────────────────────────────────────────────────────


def test_parse_line_model():
    assert parse_line("/model openai/gpt-4o") == Command("model", "openai/gpt-4o")


def test_parse_line_commands():
    assert parse_line("/quit") == Command("quit")
    assert parse_line("/exit") == Command("quit")
    assert parse_line("/help") == Command("help")
    assert parse_line("/history") == Command("history")
    assert parse_line("/undo") == Command("undo")
    assert parse_line("/compact focus on the bug") == Command("compact", "focus on the bug")
    assert parse_line("/fork abc123") == Command("fork", "abc123")


def test_parse_line_prompt():
    assert parse_line("hello world") == Prompt("hello world")
    assert parse_line("  hi  ") == Prompt("hi")


def test_parse_line_empty():
    assert parse_line("") == Prompt("")
    assert parse_line("   ") == Prompt("")


# ── render_event ────────────────────────────────────────────────────


def test_render_event_text():
    event = LoopEvent(kind="text", text="hi")
    assert _render_text(event) == "hi"


def test_render_event_markdown():
    event = LoopEvent(kind="text", text="# Title\n\n- item 1\n- item 2")
    result = _render_text(event)
    assert "Title" in result
    assert "item 1" in result


def test_render_event_thinking():
    event = LoopEvent(kind="thinking", thinking="hmm let me think")
    assert "hmm let me think" in _render_text(event)


def test_render_event_bash_request():
    event = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="bash", arguments={"command": "ls -la"})
        ),
    )
    rendered = render_event(event)
    assert isinstance(rendered, Panel)
    assert rendered.border_style == "green"


def test_render_event_read_request():
    event = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="read", arguments={"path": "a.txt"})
        ),
    )
    rendered = render_event(event)
    assert isinstance(rendered, Panel)
    assert rendered.border_style == "bright_blue"


def test_render_event_write_request():
    event = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="write", arguments={"path": "b.txt", "content": "x"})
        ),
    )
    rendered = render_event(event)
    assert isinstance(rendered, Panel)
    assert rendered.border_style == "yellow"


def test_render_event_edit_formats_code_fields():
    """edit requests render old_text/new_text as labeled code blocks."""
    event = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(
                id="c1",
                name="edit",
                arguments={
                    "path": "todo.html",
                    "old_text": "<button>old</button>",
                    "new_text": "<button>new</button>\n<div>more</div>",
                },
            )
        ),
    )
    rendered = render_event(event)
    assert isinstance(rendered, Panel)
    assert "edit" in rendered.title
    assert "todo.html" in rendered.title
    parts = rendered.renderable.renderables
    body = "".join(
        part.code if isinstance(part, Syntax) else str(part) for part in parts
    )
    assert "── old_text" in body
    assert "── new_text" in body
    assert "<button>old</button>" in body
    assert "<div>more</div>" in body


def test_render_event_other_tool_args_pretty_json():
    event = _tool_request_event("grep", {"pattern": "foo", "limit": 5})
    result = _render_text(event)
    assert '"pattern": "foo"' in result
    assert "\n" in result  # multi-line, not a one-line dict repr


def test_render_event_unknown_tool_uses_cyan():
    event = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="unknown_tool", arguments={})
        ),
    )
    rendered = render_event(event)
    assert isinstance(rendered, Panel)
    assert rendered.border_style == "cyan"


def _tool_request_event(name: str, arguments: dict) -> LoopEvent:
    return LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name=name, arguments=arguments)
        ),
    )


def test_render_event_bash_result():
    event = LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(
            tool_call_id="c1", name="bash", content="file1.txt\n[exit code: 0]"
        ),
    )
    result = _render_text(event)
    assert "file1.txt" in result


def test_render_event_read_result_hidden():
    event = LoopEvent(
        kind="tool_result",
        tool_result=ToolResultPayload(
            tool_call_id="c1", name="read", content="hello world from file"
        ),
    )
    assert render_event(event) is None


def test_render_event_usage():
    event = LoopEvent(kind="usage", usage=Usage(input_tokens=10, output_tokens=5))
    result = _render_text(event)
    assert "10" in result
    assert "5" in result


def test_render_event_returns_rich_renderable():
    """render_event returns Rich objects, not raw strings."""
    event = LoopEvent(kind="text", text="# Title")
    rendered = render_event(event)
    # Rich Markdown objects are not plain strings
    assert not isinstance(rendered, str)

    event2 = LoopEvent(kind="thinking", thinking="hmm")
    rendered2 = render_event(event2)
    assert isinstance(rendered2, Text)

    event3 = LoopEvent(
        kind="tool_request",
        tool_request=ToolRequestPayload(
            tool_call=ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        ),
    )
    rendered3 = render_event(event3)
    assert isinstance(rendered3, Panel)

