"""Modal provider-picker screen for the TUI (/provider with no argument).

Lists every registered provider; picking one dismisses with its id so the
caller can open its setup form. Escape dismisses with ``None``.
"""

from __future__ import annotations

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css


def format_provider_option(
    display_name: str,
    description: str,
    *,
    configured: bool = False,
    active: bool = False,
) -> str:
    """Return the display prompt for one provider entry (always one line)."""
    status = "[green]\u2713[/]" if configured else "[dim]\u2717[/]"
    marker = " [cyan]\u25b6 current[/]" if active else ""
    # Visible overhead besides the description: status + name + markers.
    overhead = (
        len("\u2713  ") + len(display_name) + len("  ")
        + (len(" \u25b6 current") if active else 0)
    )
    desc = fit_text(description, MAX_ROW_WIDTH - overhead)
    return f"{status}  [bold]{display_name}[/]  [dim]{desc}[/]{marker}"


class ProviderPickerScreen(PickerScreen[str | None]):
    """A modal screen listing providers; dismisses with the chosen id."""

    CSS = picker_css(
        "ProviderPickerScreen",
        "provider-picker-dialog",
        "provider-picker-list",
    )

    dialog_id = "provider-picker-dialog"
    list_id = "provider-picker-list"

    def options(self) -> list[str]:
        return [
            format_provider_option(
                entry["display_name"],
                entry["description"],
                configured=entry.get("configured", False),
                active=entry.get("active", False),
            )
            for entry in self._entries
        ]

    def result_for(self, index: int) -> str | None:
        return self._entries[index]["id"]
