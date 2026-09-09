"""In-memory todo tracking: a shared list plus the agent-facing ``todo`` tool.

The list lives only for the life of the process (no persistence): the owning
``AgentSession`` creates one :class:`TodoList`, hands it to the ``TodoTool``,
and the TUI reads it back to render the read-only side panel.
"""

from __future__ import annotations

import itertools

from pydantic import BaseModel

from .tools import ToolOutcome


class TodoItem(BaseModel):
    """A single tracked task."""

    id: str
    text: str
    status: str = "pending"  # "pending" | "in_progress" | "completed"


class TodoList:
    """An ordered, in-memory list of todos shared by the tool and the UI."""

    def __init__(self) -> None:
        self._items: list[TodoItem] = []
        self._ids = itertools.count(1)

    def add(self, text: str) -> TodoItem:
        """Append a pending todo and return it."""
        item = TodoItem(id=f"t{next(self._ids)}", text=text)
        self._items.append(item)
        return item

    def update(
        self, todo_id: str, *, status: str | None = None, text: str | None = None
    ) -> TodoItem | None:
        """Update a todo's status and/or text; ``None`` when the id is unknown."""
        item = self.get(todo_id)
        if item is None:
            return None
        if status is not None:
            item.status = status
        if text is not None:
            item.text = text
        return item

    def get(self, todo_id: str) -> TodoItem | None:
        """Return the todo with ``todo_id``, or ``None``."""
        for item in self._items:
            if item.id == todo_id:
                return item
        return None

    def all(self) -> list[TodoItem]:
        """Return every todo in insertion order."""
        return list(self._items)

    def clear_completed(self) -> int:
        """Drop every completed todo; return how many were removed."""
        before = len(self._items)
        self._items = [i for i in self._items if i.status != "completed"]
        return before - len(self._items)

    def __len__(self) -> int:
        return len(self._items)


_VALID_STATUSES = ("pending", "in_progress", "completed")


def format_todos(items: list[TodoItem]) -> str:
    """Render todos as one ``[id] status — text`` line each."""
    if not items:
        return "(no todos)"
    return "\n".join(f"[{i.id}] {i.status} — {i.text}" for i in items)


class TodoTool:
    """Add, update, list, or clear the session's in-memory todos."""

    name = "todo"
    description = (
        "Track multi-step work: add a todo, update its status "
        "(pending|in_progress|completed), list all todos, or clear completed ones."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "update", "list", "clear"],
            },
            "text": {"type": "string"},
            "id": {"type": "string"},
            "status": {"type": "string", "enum": list(_VALID_STATUSES)},
        },
        "required": ["action"],
    }

    def __init__(self, todos: TodoList | None = None) -> None:
        # NB: `todos or TodoList()` would be wrong — an empty TodoList is
        # falsy via __len__, so an explicitly shared list would be discarded.
        self._todos = todos if todos is not None else TodoList()

    @property
    def todos(self) -> TodoList:
        """The shared list this tool reads and writes."""
        return self._todos

    async def run(self, arguments: dict) -> ToolOutcome:
        action = arguments.get("action", "")
        if action == "add":
            text = (arguments.get("text") or "").strip()
            if not text:
                return ToolOutcome(
                    content="error: todo add needs a text", is_error=True
                )
            item = self._todos.add(text)
            return ToolOutcome(content=f"added [{item.id}] pending — {item.text}")
        if action == "update":
            todo_id = arguments.get("id", "")
            status = arguments.get("status")
            text = arguments.get("text")
            if status is not None and status not in _VALID_STATUSES:
                return ToolOutcome(
                    content=f"error: unknown status: {status}", is_error=True
                )
            if status is None and text is None:
                return ToolOutcome(
                    content="error: todo update needs a status and/or text",
                    is_error=True,
                )
            updated = self._todos.update(todo_id, status=status, text=text)
            if updated is None:
                return ToolOutcome(
                    content=f"error: unknown todo id: {todo_id}", is_error=True
                )
            return ToolOutcome(
                content=f"updated [{updated.id}] {updated.status} — {updated.text}"
            )
        if action == "list":
            return ToolOutcome(content=format_todos(self._todos.all()))
        if action == "clear":
            removed = self._todos.clear_completed()
            return ToolOutcome(content=f"cleared {removed} completed todo(s)")
        return ToolOutcome(content=f"error: unknown action: {action}", is_error=True)
