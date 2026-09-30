"""Modal history-picker screen for the TUI (/history).

Lists every node on the active branch; picking one (Enter/click) dismisses
with its branch index so the caller can fork to it. Escape dismisses with
``None`` (no navigation).
"""

from __future__ import annotations

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css


def format_history_option(
    index: int, kind: str, summary: str, *, is_current: bool = False
) -> str:
    """Return the display prompt for one history entry (always one line)."""
    marker = " [cyan]\u25b6 current[/]" if is_current else ""
    prefix_len = len(f"{index:>3}  {kind}  ") + (len(" \u25b6 current") if is_current else 0)
    summary = fit_text(summary, MAX_ROW_WIDTH - prefix_len)
    return f"[dim]{index:>3}[/]  [bold]{kind}[/]  {summary}{marker}"


class HistoryPickerScreen(PickerScreen[int | None]):
    """A modal screen listing branch nodes; dismisses with the chosen index."""

    CSS = picker_css(
        "HistoryPickerScreen",
        "history-picker-dialog",
        "history-picker-list",
        "history-picker-search",
    )

    dialog_id = "history-picker-dialog"
    list_id = "history-picker-list"
    search_id = "history-picker-search"
    search_placeholder = "Filter history..."

    def __init__(self, entries: list[dict]) -> None:
        super().__init__(entries)
        # The current node is last; highlight it so Enter re-selects it.
        self.initial_highlight = len(entries) - 1

    def options(self) -> list[str]:
        return [
            format_history_option(
                entry["index"],
                entry["kind"],
                entry["summary"],
                is_current=entry.get("is_current", False),
            )
            for entry in self._entries
        ]

    def result_for(self, index: int) -> int | None:
        return self._entries[index]["index"]
