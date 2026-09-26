"""The tailoring domain's binding of the shared AI provider boundary.

Everything genuinely vendor-specific lives in `app/services/ai/providers/base.py`
and does not know a resume exists. This module is the tailoring-specific sliver
on top of it: which settings key selects a provider (`TAILORING_AI_PROVIDER`) and
which adapter `"anthropic"` resolves to (`anthropic_provider.AnthropicProvider`,
itself a thin binding of `TAILORING_AI_MODEL` and friends onto the shared adapter).
`"openrouter"` resolves to `openrouter_provider.OpenRouterProvider` — registered
for this domain only, as a second vendor for evaluation.
The mirror of `app/services/job/providers/base.py`, bound to `TAILORING_AI_*`.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.ai.providers.base import (
    PROVIDER_AUTHENTICATION_FAILED,
    PROVIDER_BILLING_BLOCKED,
    PROVIDER_CONTENT_REJECTED,
    PROVIDER_ERROR,
    PROVIDER_ERROR_CODES,
    PROVIDER_INVALID_REQUEST,
    PROVIDER_NOT_CONFIGURED,
    PROVIDER_OUTPUT_MALFORMED,
    PROVIDER_RATE_LIMITED,
    PROVIDER_SCHEMA_INVALID,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNAVAILABLE,
    ProviderError,
    StructuredCompletion,
    StructuredOutputProvider,
    UnconfiguredProvider,
    resolve_provider,
)

__all__ = [
    "PROVIDER_AUTHENTICATION_FAILED",
    "PROVIDER_BILLING_BLOCKED",
    "PROVIDER_CONTENT_REJECTED",
    "PROVIDER_ERROR",
    "PROVIDER_ERROR_CODES",
    "PROVIDER_INVALID_REQUEST",
    "PROVIDER_NOT_CONFIGURED",
    "PROVIDER_OUTPUT_MALFORMED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_SCHEMA_INVALID",
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAUTHORIZED",
    "PROVIDER_UNAVAILABLE",
    "ProviderError",
    "StructuredCompletion",
    "StructuredOutputProvider",
    "UnconfiguredProvider",
    "get_provider",
]


def get_provider(name: str | None = None) -> StructuredOutputProvider:
    """The configured adapter.

    The Anthropic adapter is imported lazily, from inside the factory, so that
    the vendor SDK is only loaded when it is the one selected — a deployment
    using a different provider should not need the other one installed.
    """

    def _anthropic() -> StructuredOutputProvider:
        from app.services.tailoring.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    def _openrouter() -> StructuredOutputProvider:
        from app.services.tailoring.providers.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider()

    return resolve_provider(
        name or settings.TAILORING_AI_PROVIDER,
        anthropic_factory=_anthropic,
        openrouter_factory=_openrouter,
    )
