"""Modal skill-picker screen for the TUI (/skills).

Lists every loaded ``SKILL.md`` skill; picking one (Enter/click) dismisses
with its name so the caller can insert it into the input bar. Escape
dismisses with ``None`` (no insertion).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList


#: Maximum visible characters per skill row (name + description). Rows are
#: truncated with "..." so each skill occupies exactly one line.
MAX_OPTION_WIDTH = 76


def format_skill_option(name: str, description: str = "") -> str:
    """Return the display prompt for one skill entry, always one line.

    The description is collapsed to one line and cut with ``...`` so the
    whole row (name + description) fits ``MAX_OPTION_WIDTH`` characters.
    """
    desc = " ".join(description.split())
    if len(name) >= MAX_OPTION_WIDTH:
        return f"[bold]{name[: MAX_OPTION_WIDTH - 3].rstrip()}...[/]"
    budget = MAX_OPTION_WIDTH - len(name) - 2  # 2 for the separator
    if len(desc) > budget:
        desc = desc[: max(budget - 3, 0)].rstrip() + "..."
    if desc:
        return f"[bold]{name}[/]  [dim]{desc}[/]"
    return f"[bold]{name}[/]"


class SkillPickerScreen(ModalScreen[str | None]):
    """A modal screen listing loaded skills; dismisses with the chosen name."""

    CSS = """
    SkillPickerScreen {
        align: center middle;
        background: $background 60%;
    }
    #skill-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 70%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #skill-picker-list {
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
        with Vertical(id="skill-picker-dialog"):
            option_list = OptionList(id="skill-picker-list")
            for entry in self._entries:
                option_list.add_option(
                    format_skill_option(
                        entry["name"], entry.get("description", "")
                    )
                )
            option_list.highlighted = 0
            yield option_list
        yield Footer()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Dismiss with the selected skill name."""
        index = getattr(event, "option_index", None)
        if index is None:
            index = getattr(event, "index")
        self.dismiss(self._entries[int(index)]["name"])

    def action_cancel(self) -> None:
        """Dismiss without inserting anything."""
        self.dismiss(None)
