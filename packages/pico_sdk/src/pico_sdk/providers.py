"""Provider construction from settings and per-provider stored config."""

from __future__ import annotations

import os
import warnings
from typing import Any

from pico_ai.providers import create_provider as build_adapter
from pico_ai.providers import get_spec, provider_ids

from .config import Settings, load_settings


def effective_config(provider_id: str, settings: Settings) -> dict[str, str]:
    """Resolve the working config for ``provider_id``.

    Precedence: field default < environment variable < stored settings value.
    Clearing a stored value falls back to env/default.
    """
    spec = get_spec(provider_id)
    stored = settings.providers.get(provider_id) or {}
    resolved: dict[str, str] = {}
    for f in spec.fields:
        value = stored.get(f.key, "")
        if not value and f.env_var:
            value = os.environ.get(f.env_var, "")
        if not value:
            value = f.default
        resolved[f.key] = value
    return resolved


def missing_required(provider_id: str, settings: Settings) -> list[str]:
    """Return labels of required fields with no effective value."""
    spec = get_spec(provider_id)
    config = effective_config(provider_id, settings)
    return [f.label for f in spec.required_fields if not config.get(f.key)]


def create_provider(settings: Settings | None = None) -> Any:
    """Build the active provider from settings (id + stored config)."""
    settings = settings or load_settings()
    provider_id = settings.provider or "openrouter"
    if provider_id not in provider_ids():
        warnings.warn(
            f"unknown provider {provider_id!r}; falling back to 'openrouter'",
            stacklevel=2,
        )
        provider_id = "openrouter"
    return build_adapter(provider_id, effective_config(provider_id, settings))


def describe_providers(settings: Settings) -> list[dict]:
    """Return picker rows: id, display name, description, configured, active."""
    rows = []
    for pid in provider_ids():
        spec = get_spec(pid)
        rows.append(
            {
                "id": pid,
                "display_name": spec.display_name,
                "description": spec.description,
                "configured": not missing_required(pid, settings),
                "active": pid == (settings.provider or "openrouter"),
            }
        )
    return rows


FREE_MODEL_ALIAS = "openrouter/free"


async def resolve_free_model(provider: Any) -> str | None:
    """Resolve the ``openrouter/free`` alias to a concrete free model id.

    Picks the first free model (alphabetically) that supports tool calling;
    falls back to any free model. Returns None if nothing free is available
    or the lookup fails — callers should keep the alias as-is in that case.
    """
    try:
        models = await provider.list_models()
    except Exception:
        return None
    free = sorted(
        (m for m in models if m.get("is_free", False)),
        key=lambda m: str(m.get("name", "")).lower(),
    )
    with_tools = [m for m in free if m.get("supports_tools", True)]
    if with_tools:
        return with_tools[0]["id"]
    return free[0]["id"] if free else None
