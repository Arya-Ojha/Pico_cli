"""Provider specs: declarative metadata driving the TUI setup form.

Each provider module (``openai.py``, ``anthropic.py``, …) exposes a
``ProviderSpec``: an id, display name, the config fields the setup form
renders (api key, base url, model, …), a default model, and a ``create``
factory that builds the adapter from a config dict. The adapter itself only
converts between that provider's wire format and the app's default
``StreamEvent`` shape (see ``pico_ai.types``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldSpec:
    """One config field rendered as a row in the provider setup form."""

    key: str  # config key, e.g. "api_key"
    label: str  # human label, e.g. "API key"
    default: str = ""
    secret: bool = False  # masked input in the form
    required: bool = False  # must be non-empty (after env fallback) to use
    env_var: str = ""  # environment fallback, e.g. "OPENAI_API_KEY"
    placeholder: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the host needs to configure, build, and list a provider."""

    id: str  # stable id, e.g. "openai"
    display_name: str  # shown in the picker and status bar, e.g. "OpenAI"
    description: str  # one-line picker blurb
    fields: tuple[FieldSpec, ...] = ()
    default_model: str = ""
    create: Callable[[dict[str, str]], Any] = field(default=lambda _c: None)

    @property
    def required_fields(self) -> tuple[FieldSpec, ...]:
        """Fields that must resolve non-empty for the provider to work."""
        return tuple(f for f in self.fields if f.required)
