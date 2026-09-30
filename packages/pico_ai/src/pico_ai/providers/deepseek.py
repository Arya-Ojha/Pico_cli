"""DeepSeek provider: OpenAI-compatible wire format → app default ``StreamEvent``.

DeepSeek's ``reasoning_content`` delta field carries the model's thinking and
is normalized to ``thinking`` events by the shared compat base.
"""

from __future__ import annotations

import httpx

from ._compat import OpenAICompatProvider
from .spec import FieldSpec, ProviderSpec

PROVIDER_ID = "deepseek"
DISPLAY_NAME = "DeepSeek"
DESCRIPTION = "DeepSeek API (chat + reasoner)."
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="api_key",
        label="API key",
        secret=True,
        required=True,
        env_var="DEEPSEEK_API_KEY",
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


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek chat-completions adapter."""

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


def create(config: dict[str, str]) -> DeepSeekProvider:
    """Build the adapter from a setup-form config dict."""
    return DeepSeekProvider(
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
