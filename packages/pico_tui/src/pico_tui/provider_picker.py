"""Modal provider-picker screen for the TUI (/provider with no argument).

Lists every registered provider; picking one dismisses with its id so the
caller can open its setup form. Escape dismisses with ``None``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList


def format_provider_option(
    display_name: str,
    description: str,
    *,
    configured: bool = False,
    active: bool = False,
) -> str:
    """Return the display prompt for one provider entry."""
    status = "[green]\u2713[/]" if configured else "[dim]\u2717[/]"
    marker = " [cyan]\u25b6 current[/]" if active else ""
    return f"{status}  [bold]{display_name}[/]  [dim]{description}[/]{marker}"


class ProviderPickerScreen(ModalScreen[str | None]):
    """A modal screen listing providers; dismisses with the chosen id."""

    CSS = """
    ProviderPickerScreen {
        align: center middle;
        background: $background 60%;
    }
    #provider-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 60%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #provider-picker-list {
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
        with Vertical(id="provider-picker-dialog"):
            option_list = OptionList(id="provider-picker-list")
            for entry in self._entries:
                option_list.add_option(
                    format_provider_option(
                        entry["display_name"],
                        entry["description"],
                        configured=entry.get("configured", False),
                        active=entry.get("active", False),
                    )
                )
            option_list.highlighted = 0
            yield option_list
        yield Footer()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Dismiss with the selected provider id."""
        # Textual renamed the index attribute across versions.
        index = getattr(event, "option_index", None)
        if index is None:
            index = getattr(event, "index")
        self.dismiss(self._entries[int(index)]["id"])

    def action_cancel(self) -> None:
        """Dismiss without changing the provider."""
        self.dismiss(None)
