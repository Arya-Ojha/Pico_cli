"""Read-only todo side panel for the TUI.

The panel renders the session's in-memory todos on the right side of the
chat. It hides itself while the list is empty and appears as soon as the
agent adds the first todo.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

# Glyph + style per todo status.
_STATUS_MARKS: dict[str, tuple[str, str]] = {
    "pending": ("\u25cb", "dim"),
    "in_progress": ("\u25d0", "yellow"),
    "completed": ("\u25cf", "green"),
}


class TodoPanel(Static):
    """A read-only side panel showing the session's todos."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.display = False

    def update_todos(self, items: list) -> None:
        """Re-render the panel; hide it while there is nothing to show."""
        if not items:
            self.display = False
            return
        self.display = True
        body = Text()
        body.append("Todos", style="bold underline")
        for item in items:
            status = getattr(item, "status", "pending")
            mark, style = _STATUS_MARKS.get(status, ("?", "dim"))
            text = str(getattr(item, "text", ""))
            if len(text) > 26:
                text = text[:25] + "\u2026"
            body.append("\n")
            body.append(f"{mark} ", style=style)
            body.append(text)
        self.update(body)
