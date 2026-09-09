"""Rich-based event renderer for LoopEvent → Rich renderable."""

from __future__ import annotations

import json

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from pico_sdk import LoopEvent

# Distinct heading colors per tool type.
_TOOL_COLORS: dict[str, str] = {
    "bash": "green",
    "read": "bright_blue",
    "write": "yellow",
    "edit": "magenta",
}


def _truncate(text: str, limit: int = 200) -> str:
    """Collapse whitespace and truncate long tool output."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def render_event(event: LoopEvent):  # -> RenderableType (kept loose for mypy simplicity)
    """Convert a LoopEvent to a Rich renderable for display in a RichLog.

    Returns None when the event should produce no visible output.
    """
    if event.kind == "text":
        return (
            Markdown(event.text)
            if _looks_like_markdown(event.text)
            else Text(event.text)
        )
    if event.kind == "thinking":
        return Text(event.thinking, style="dim italic")
    if event.kind == "tool_request" and event.tool_request is not None:
        call = event.tool_request.tool_call
        color = _TOOL_COLORS.get(call.name, "cyan")
        if call.name == "bash":
            cmd = call.arguments.get("command", "")
            return Panel(
                cmd,
                title=f"[bold {color}]{call.name}[/]",
                border_style=color,
                title_align="left",
            )
        if call.name in ("edit", "write"):
            return _edit_write_panel(call.name, call.arguments, color)
        return Panel(
            _pretty_args(call.arguments),
            title=f"[bold {color}]{call.name}[/]",
            border_style=color,
            title_align="left",
        )
    if event.kind == "tool_result" and event.tool_result is not None:
        result = event.tool_result
        color = _TOOL_COLORS.get(result.name, "dim cyan")
        if result.name == "bash":
            return Panel(
                result.content.rstrip(),
                title=f"[{color}]{result.name} result[/]",
                border_style=color,
                title_align="left",
            )
        snippet = _truncate(result.content)
        return Panel(
            snippet,
            title=f"[{color}]{result.name} result[/]",
            border_style=color,
            title_align="left",
        )
    if event.kind == "usage" and event.usage is not None:
        u = event.usage
        return Text(f"({u.input_tokens}↓ {u.output_tokens}↑)", style="dim")
    return None


def _looks_like_markdown(text: str) -> bool:
    """Heuristic: use Rich Markdown for multi-line or formatted responses."""
    return "\n" in text or any(
        marker in text for marker in ("```", "**", "# ", "- ", "1. ", "|")
    )


# Keys that carry code content in edit/write tool arguments, in display order.
_CODE_KEYS = ("old_text", "new_text", "content")


def _syntax_for(text: str, path: str) -> Syntax:
    """Render text as code with the language guessed from the file path."""
    lexer = None
    if "." in path.rsplit("/", 1)[-1] and path.rsplit("/", 1)[-1].split(".")[-1]:
        ext = path.rsplit(".", 1)[-1].lower()
        ext_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "html": "html", "css": "css", "json": "json", "md": "markdown",
            "yml": "yaml", "yaml": "yaml", "toml": "toml", "rs": "rust",
            "go": "go", "java": "java", "c": "c", "cpp": "cpp", "sh": "bash",
        }
        lexer = ext_map.get(ext)
    return Syntax(
        text,
        lexer or "text",
        word_wrap=True,
        theme="ansi_dark",
        background_color="default",
    )


def _edit_write_panel(name: str, arguments: dict, color: str) -> Panel:
    """Format an edit/write request: path in the title, fields as code blocks."""
    path = str(arguments.get("path", ""))
    parts: list = []
    for key in _CODE_KEYS:
        if key in arguments and arguments[key] != "":
            parts.append(Text(f"── {key} ", style=f"bold {color}"))
            parts.append(_syntax_for(str(arguments[key]), path))
    if not parts:  # no code fields (e.g. a delete or move) — show args
        parts.append(_pretty_args(arguments))
    title = f"[bold {color}]{name}[/] {path}" if path else f"[bold {color}]{name}[/]"
    return Panel(
        Group(*parts),
        title=title,
        border_style=color,
        title_align="left",
    )


def _pretty_args(arguments: dict) -> str:
    """Indent-2 JSON dump so other tools' args stay readable."""
    try:
        return json.dumps(arguments, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)
