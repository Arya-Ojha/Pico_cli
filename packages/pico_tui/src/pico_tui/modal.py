"""Shared chrome for all TUI modal windows (pickers + forms).

Every modal looks the same: dimmed overlay, centered dialog with a round
primary border on the surface background, and a footer. List pickers share
the ``PickerScreen`` base (standard cancel bindings, dialog + option list
+ footer layout, index-based dismissal); subclasses only provide the
formatted rows and the index → result mapping. Rows are single-line:
free-text fields are fitted with :func:`fit_text` so nothing wraps.
"""

from __future__ import annotations

import re
from typing import TypeVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList

#: Maximum visible characters per picker row (name + free text). Rows that
#: would exceed it are cut with "..." so each entry is exactly one line.
MAX_ROW_WIDTH = 76


def fit_text(text: str, budget: int) -> str:
    """Collapse ``text`` to one line and cut it to ``budget`` chars + ``...``."""
    text = " ".join(text.split())
    if len(text) <= budget:
        return text
    return text[: max(budget - 3, 0)].rstrip() + "..."


def picker_css(screen: str, dialog_id: str, list_id: str, search_id: str) -> str:
    """Return the standard picker CSS for the given widget names."""
    return f"""
    {screen} {{
        align: center middle;
        background: $background 60%;
    }}
    #{dialog_id} {{
        width: 80%;
        max-width: 100;
        height: 70%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }}
    #{search_id} {{
        margin-bottom: 1;
    }}
    #{list_id} {{
        height: 1fr;
        border: none;
        background: $surface;
    }}
    """


def form_css(screen: str, dialog_id: str) -> str:
    """Return the standard form CSS (content-sized height, roomier padding)."""
    return f"""
    {screen} {{
        align: center middle;
        background: $background 60%;
    }}
    #{dialog_id} {{
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }}
    #{dialog_id} Label {{
        margin-top: 1;
    }}
    #{dialog_id} Button {{
        margin: 0 1;
    }}
    """


T = TypeVar("T")


class PickerScreen(ModalScreen[T]):
    """Base class for single-list modal pickers with a filter bar.

    Subclasses set :attr:`dialog_id` / :attr:`list_id` / :attr:`search_id`
    (kept stable — tests query them), provide :meth:`options` (one
    single-line row per entry) and :meth:`result_for` (entry index →
    dismiss value), and optionally :attr:`initial_highlight` (default:
    first row) and :attr:`search_placeholder`.

    Typing in the filter bar narrows the list (case-insensitive substring
    on the visible row text). Arrow keys move the highlight even while
    the filter bar is focused; Enter picks the highlighted row; Escape
    cancels. Printable keys (including ``q``) typed in the filter bar
    never trigger screen bindings.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    dialog_id: str = "picker-dialog"
    list_id: str = "picker-list"
    search_id: str = "picker-search"
    search_placeholder: str = "Type to filter..."
    initial_highlight: int = 0

    def __init__(self, entries: list[dict]) -> None:
        super().__init__()
        self._entries = entries
        self._visible: list[int] = list(range(len(entries)))

    def options(self) -> list[str]:
        """Return one formatted single-line row per entry."""
        raise NotImplementedError

    def result_for(self, index: int) -> T:
        """Map an entry index to the value to dismiss with."""
        raise NotImplementedError

    @staticmethod
    def _plain(row: str) -> str:
        """Strip Rich markup so filtering matches visible text."""
        return re.sub(r"\[.*?\]", "", row)

    def compose(self) -> ComposeResult:
        with Vertical(id=self.dialog_id):
            yield Input(placeholder=self.search_placeholder, id=self.search_id)
            option_list = OptionList(id=self.list_id)
            for row in self.options():
                option_list.add_option(row)
            option_list.highlighted = self.initial_highlight
            yield option_list
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#" + self.search_id, Input).focus()

    def _option_list(self) -> OptionList:
        return self.query_one("#" + self.list_id, OptionList)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Narrow the list to rows containing the filter text."""
        query = event.value.strip().lower()
        rows = self.options()
        self._visible = [
            i for i, row in enumerate(rows) if query in self._plain(row).lower()
        ]
        option_list = self._option_list()
        option_list.clear_options()
        for i in self._visible:
            option_list.add_option(rows[i])
        if self._visible:
            option_list.highlighted = 0

    def _pick_visible(self, pos: int) -> None:
        if 0 <= pos < len(self._visible):
            self.dismiss(self.result_for(self._visible[pos]))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the filter bar picks the highlighted row."""
        # Never let this bubble to the app's own submit handler — the
        # filter text is not a prompt.
        event.stop()
        if not self._visible:
            return
        highlighted = self._option_list().highlighted
        self._pick_visible(highlighted if highlighted is not None else 0)

    def action_cursor_up(self) -> None:
        if self._visible:
            self._option_list().action_cursor_up()

    def action_cursor_down(self) -> None:
        if self._visible:
            self._option_list().action_cursor_down()

    def _selected_index(self, event: OptionList.OptionSelected) -> int:
        # Textual renamed the index attribute across versions; getattr
        # keeps both spellings working.
        index = getattr(event, "option_index", None)
        if index is None:
            index = getattr(event, "index")
        return int(index)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Dismiss with the mapped value for the selected (filtered) row."""
        self._pick_visible(self._selected_index(event))

    def action_cancel(self) -> None:
        """Dismiss without picking anything."""
        self.dismiss(None)
