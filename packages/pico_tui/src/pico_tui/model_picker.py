"""Modal model-picker screen for the TUI (/model with no argument)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, OptionList


def sort_models(models: list[dict]) -> list[dict]:
    """Sort models with free ones first, then alphabetically by name."""
    return sorted(models, key=lambda m: (not m["is_free"], m["name"].lower()))


def format_model_option(model: dict, current: str = "") -> str:
    """Return the display prompt for one model entry."""
    flag = "[bold green]FREE[/]" if model["is_free"] else ""
    marker = " [cyan]\u2713 current[/]" if model["id"] == current else ""
    parts = [f"{model['name']}  [dim]{model['id']}[/]{marker}"]
    if flag:
        parts.append(flag)
    return "  ".join(parts)


class ModelPickerScreen(ModalScreen[str | None]):
    """A modal screen listing available models; dismisses with the chosen id."""

    CSS = """
    ModelPickerScreen {
        align: center middle;
        background: $background 60%;
    }
    #model-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 70%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    #model-picker-list {
        height: 1fr;
        border: none;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
    ]

    def __init__(self, models: list[dict], current: str = "") -> None:
        super().__init__()
        self._models = sort_models(models)
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-dialog"):
            option_list = OptionList(id="model-picker-list")
            for model in self._models:
                option_list.add_option(format_model_option(model, self._current))
            option_list.highlighted = 0
            yield option_list
        yield Footer()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Dismiss with the selected model id."""
        # Textual renamed the index attribute across versions; getattr keeps
        # both spellings working (and keeps mypy happy — the attribute only
        # exists on the event instance, not the stubbed class surface).
        index = getattr(event, "option_index", None)
        if index is None:
            index = getattr(event, "index")
        self.dismiss(self._models[int(index)]["id"])

    def action_cancel(self) -> None:
        """Dismiss without changing the model."""
        self.dismiss(None)
