"""Modal model-picker screen for the TUI (/model with no argument)."""

from __future__ import annotations

from .modal import MAX_ROW_WIDTH, PickerScreen, fit_text, picker_css


def sort_models(models: list[dict]) -> list[dict]:
    """Sort models with free ones first, then alphabetically by name."""
    return sorted(models, key=lambda m: (not m["is_free"], m["name"].lower()))


def format_model_option(model: dict, current: str = "") -> str:
    """Return the display prompt for one model entry (always one line)."""
    is_current = model["id"] == current
    flag = "  [bold green]FREE[/]" if model["is_free"] else ""
    marker = " [cyan]\u2713 current[/]" if is_current else ""
    # Visible overhead besides name + id: separator + markers (+ the join
    # separator when the FREE flag is present).
    markers_len = (len("  FREE") if model["is_free"] else 0) + (
        len(" \u2713 current") if is_current else 0
    ) + (2 if model["is_free"] else 0)
    model_id = model["id"]
    if len(model_id) > MAX_ROW_WIDTH - 2 - 3 - markers_len:
        # Freakishly long id: keep the distinguishing tail; selection
        # still uses the full stored id.
        keep = MAX_ROW_WIDTH - 2 - 3 - markers_len - 3
        model_id = "..." + model_id[-max(keep, 0) :]
    name = fit_text(model["name"], MAX_ROW_WIDTH - 2 - len(model_id) - markers_len)
    parts = [f"{name}  [dim]{model_id}[/]{marker}"]
    if flag:
        parts.append(flag)
    return "  ".join(parts)


class ModelPickerScreen(PickerScreen[str | None]):
    """A modal screen listing available models; dismisses with the chosen id."""

    CSS = picker_css(
        "ModelPickerScreen",
        "model-picker-dialog",
        "model-picker-list",
        "model-picker-search",
    )

    dialog_id = "model-picker-dialog"
    list_id = "model-picker-list"
    search_id = "model-picker-search"
    search_placeholder = "Filter models..."

    def __init__(self, models: list[dict], current: str = "") -> None:
        super().__init__(sort_models(models))
        self._current = current

    def options(self) -> list[str]:
        return [
            format_model_option(model, self._current)
            for model in self._entries
        ]

    def result_for(self, index: int) -> str | None:
        return self._entries[index]["id"]
