"""OpenAI provider: chat-completions wire format → app default ``StreamEvent``."""

from __future__ import annotations

import httpx

from ._compat import OpenAICompatProvider
from .spec import FieldSpec, ProviderSpec

PROVIDER_ID = "openai"
DISPLAY_NAME = "OpenAI"
DESCRIPTION = "OpenAI API (GPT models)."
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="api_key",
        label="API key",
        secret=True,
        required=True,
        env_var="OPENAI_API_KEY",
        placeholder="sk-...",
    ),
    FieldSpec(
        key="base_url",
        label="Base URL",
        default=DEFAULT_BASE_URL,
        placeholder=DEFAULT_BASE_URL,
    ),
    FieldSpec(key="model", label="Default model", default=DEFAULT_MODEL),
)


class OpenAIProvider(OpenAICompatProvider):
    """OpenAI chat-completions adapter."""

    provider_id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
        first_token_timeout: float = 60.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            client=client,
            timeout=timeout,
            first_token_timeout=first_token_timeout,
        )


def create(config: dict[str, str]) -> OpenAIProvider:
    """Build the adapter from a setup-form config dict."""
    return OpenAIProvider(
        api_key=config.get("api_key", ""),
        base_url=config.get("base_url", "") or DEFAULT_BASE_URL,
    )


SPEC = ProviderSpec(
    id=PROVIDER_ID,
    display_name=DISPLAY_NAME,
    description=DESCRIPTION,
    fields=FIELDS,
    default_model=DEFAULT_MODEL,
    create=create,
)
