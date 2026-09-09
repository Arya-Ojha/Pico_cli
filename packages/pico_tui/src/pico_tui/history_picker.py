"""Modal history-picker screen for the TUI (/history).

Lists every node on the active branch; picking one (Enter/click) dismisses
with its branch index so the caller can fork to it. Escape dismisses with
``None`` (no navigation).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList


def format_history_option(
    index: int, kind: str, summary: str, *, is_current: bool = False
) -> str:
    """Return the display prompt for one history entry."""
    marker = " [cyan]\u25b6 current[/]" if is_current else ""
    return f"[dim]{index:>3}[/]  [bold]{kind}[/]  {summary}{marker}"


class HistoryPickerScreen(ModalScreen[int | None]):
    """A modal screen listing branch nodes; dismisses with the chosen index."""

    CSS = """
    HistoryPickerScreen {
        align: center middle;
        background: $background 60%;
    }
    #history-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 70%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #history-picker-list {
        height: 1fr;
        border: none;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    def __init__(self, entries: list[dict]) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(id="history-picker-dialog"):
            option_list = OptionList(id="history-picker-list")
            for entry in self._entries:
                option_list.add_option(
                    format_history_option(
                        entry["index"],
                        entry["kind"],
                        entry["summary"],
                        is_current=entry.get("is_current", False),
                    )
                )
            option_list.highlighted = len(self._entries) - 1
            yield option_list
        yield Footer()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Dismiss with the selected branch index."""
        # Textual renamed the index attribute across versions.
        index = getattr(event, "option_index", None)
        if index is None:
            index = getattr(event, "index")
        self.dismiss(self._entries[index]["index"])

    def action_cancel(self) -> None:
        """Dismiss without navigating anywhere."""
        self.dismiss(None)
