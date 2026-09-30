"""Provider registry: the host-owned list of available backends (ADR-0004).

Adding a provider means adding one module next to this one (converting that
provider's wire format to the app's default ``StreamEvent`` shape) plus one
line in ``PROVIDERS`` below. This is a hardcoded list, not a plugin API —
the host keeps sovereignty over which backends exist.
"""

from __future__ import annotations

from typing import Any

from pico_ai.openrouter import OpenRouterProvider

from . import anthropic, deepseek, gemini, ollama
from . import openai as openai_provider
from .spec import FieldSpec, ProviderSpec

OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _create_openrouter(config: dict[str, str]) -> OpenRouterProvider:
    return OpenRouterProvider(
        api_key=config.get("api_key", ""),
        base_url=config.get("base_url", "") or OPENROUTER_DEFAULT_BASE_URL,
    )


OPENROUTER_SPEC = ProviderSpec(
    id="openrouter",
    display_name="OpenRouter",
    description="OpenRouter gateway (many models, one key).",
    fields=(
        FieldSpec(
            key="api_key",
            label="API key",
            secret=True,
            required=True,
            env_var="OPENROUTER_API_KEY",
            placeholder="sk-or-v1-...",
        ),
        FieldSpec(
            key="base_url",
            label="Base URL",
            default=OPENROUTER_DEFAULT_BASE_URL,
            placeholder=OPENROUTER_DEFAULT_BASE_URL,
        ),
        FieldSpec(key="model", label="Default model", default="openrouter/free"),
    ),
    default_model="openrouter/free",
    create=_create_openrouter,
)

PROVIDERS: dict[str, ProviderSpec] = {
    OPENROUTER_SPEC.id: OPENROUTER_SPEC,
    openai_provider.SPEC.id: openai_provider.SPEC,
    anthropic.SPEC.id: anthropic.SPEC,
    gemini.SPEC.id: gemini.SPEC,
    deepseek.SPEC.id: deepseek.SPEC,
    ollama.SPEC.id: ollama.SPEC,
}


def provider_ids() -> list[str]:
    """Return registered provider ids in picker order."""
    return list(PROVIDERS)


def get_spec(provider_id: str) -> ProviderSpec:
    """Return the spec for ``provider_id`` (``KeyError`` when unknown)."""
    return PROVIDERS[provider_id]


def create_provider(provider_id: str, config: dict[str, str]) -> Any:
    """Build the adapter for ``provider_id`` from a config dict."""
    return get_spec(provider_id).create(config)


__all__ = [
    "PROVIDERS",
    "ProviderSpec",
    "FieldSpec",
    "provider_ids",
    "get_spec",
    "create_provider",
]
