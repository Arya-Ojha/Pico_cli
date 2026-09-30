"""Modal skill-picker screen for the TUI (/skills).

Lists every loaded ``SKILL.md`` skill; picking one (Enter/click) dismisses
with its name so the caller can insert it into the input bar. Escape
dismisses with ``None`` (no insertion).
"""

from __future__ import annotations

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css

#: Kept for backwards compatibility; prefer ``modal.MAX_ROW_WIDTH``.
MAX_OPTION_WIDTH = MAX_ROW_WIDTH


def format_skill_option(name: str, description: str = "") -> str:
    """Return the display prompt for one skill entry (always one line).

    The description is collapsed to one line and cut with ``...`` so the
    whole row (name + description) fits ``MAX_ROW_WIDTH`` characters.
    """
    desc = fit_text(description, MAX_ROW_WIDTH - len(name) - 2)
    if len(name) >= MAX_ROW_WIDTH:
        return f"[bold]{fit_text(name, MAX_ROW_WIDTH)}[/]"
    if desc:
        return f"[bold]{name}[/]  [dim]{desc}[/]"
    return f"[bold]{name}[/]"


class SkillPickerScreen(PickerScreen[str | None]):
    """A modal screen listing loaded skills; dismisses with the chosen name."""

    CSS = picker_css(
        "SkillPickerScreen", "skill-picker-dialog", "skill-picker-list"
    )

    dialog_id = "skill-picker-dialog"
    list_id = "skill-picker-list"

    def options(self) -> list[str]:
        return [
            format_skill_option(entry["name"], entry.get("description", ""))
            for entry in self._entries
        ]

    def result_for(self, index: int) -> str | None:
        return self._entries[index]["name"]
