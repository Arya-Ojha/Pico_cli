"""Modal provider setup form for the TUI (/provider <id> or via the picker).

Renders one labeled input row per field of the provider's spec (API key,
base URL, model, …); secret fields are masked. Dismisses with the full
``{key: value}`` dict on save, or ``None`` on cancel. The caller diffs
against the pre-filled values and stores only what changed.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label

from pico_ai.providers.spec import ProviderSpec

from .modal import form_css


class ProviderFormScreen(ModalScreen[dict[str, str] | None]):
    """A modal form built from a provider spec; dismisses with the values."""

    CSS = (
        form_css("ProviderFormScreen", "provider-form-dialog")
        + """
    #provider-form-hint {
        color: $text-muted;
    }
    #provider-form-buttons {
        margin-top: 1;
        align: center middle;
    }
    """
    )

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, spec: ProviderSpec, initial: dict[str, str]) -> None:
        super().__init__()
        self._spec = spec
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-form-dialog"):
            yield Label(f"[bold]Configure {self._spec.display_name}[/]")
            yield Label(
                "Blank = fall back to environment variable or default. "
                "Ctrl+S saves, Esc cancels.",
                id="provider-form-hint",
            )
            for f in self._spec.fields:
                required = " (required)" if f.required else ""
                env_hint = f"  [dim]env: {f.env_var}[/]" if f.env_var else ""
                yield Label(f"{f.label}{required}{env_hint}")
                yield Input(
                    value=self._initial.get(f.key, ""),
                    placeholder=f.placeholder or f.default,
                    password=f.secret,
                    id=f"fld-{f.key}",
                )
            with Horizontal(id="provider-form-buttons"):
                yield Button("Save", id="provider-form-save", variant="primary")
                yield Button("Cancel", id="provider-form-cancel")
        yield Footer()

    def _collect(self) -> dict[str, str]:
        """Read every field input into a ``{key: value}`` dict."""
        values: dict[str, str] = {}
        for f in self._spec.fields:
            values[f.key] = self.query_one(f"#fld-{f.key}", Input).value
        return values

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Save or cancel via the dialog buttons."""
        if event.button.id == "provider-form-save":
            self.dismiss(self._collect())
        else:
            self.dismiss(None)

    def action_save(self) -> None:
        """Save via Ctrl+S."""
        self.dismiss(self._collect())

    def action_cancel(self) -> None:
        """Cancel via Escape."""
        self.dismiss(None)
