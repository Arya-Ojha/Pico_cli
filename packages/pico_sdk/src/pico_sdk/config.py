"""Settings loaded from ``~/.pico/settings.json`` with sensible defaults."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """User-configurable settings for the agent."""

    model: str = "openrouter/free"  # alias: best available free model
    context_window: int = 128_000
    reserve_tokens: int = 16_384
    session_dir: str = "~/.pico/sessions"
    api_key_env: str = "OPENROUTER_API_KEY"
    # Active provider id (see ``pico_ai.providers`` registry).
    provider: str = "openrouter"
    # Per-provider stored config, e.g. {"openai": {"api_key": "sk-…"}}.
    # Secrets live here in plaintext — prefer env vars for shared machines.
    providers: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Curated-extension settings (ADR-0003: hardcoded core).
    skills_dir: str = "~/.pico/skills"
    # Permission gating: which core tools the model may invoke.
    # ``None`` (default) allows every core tool; ``[]`` allows none.
    # When set, this loop-level gate wins over the per-tool ``allow_bash``
    # flag. Unknown names are ignored with a startup warning.
    allowed_tools: list[str] | None = None


def default_settings_path() -> Path:
    """Return the default settings file path."""
    return Path.home() / ".pico" / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from ``path`` (default ``~/.pico/settings.json``).

    Missing keys fall back to defaults; a missing file yields all defaults.
    """
    path = Path(path) if path is not None else default_settings_path()
    if not path.exists():
        return Settings()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Settings.model_validate(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Persist ``settings`` to ``path`` (default ``~/.pico/settings.json``)."""
    path = Path(path) if path is not None else default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings.model_dump(), indent=2) + "\n", encoding="utf-8"
    )
    return path
