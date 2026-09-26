"""The resume domain's binding of the shared AI provider boundary.

Everything genuinely vendor-specific — the error vocabulary, the protocol, the
`UnconfiguredProvider` stand-in — lives in `app/services/ai/providers/base.py`
and does not know a resume exists. This module is the resume-specific sliver on
top of it: which settings key selects a provider (`RESUME_AI_PROVIDER`) and
which adapter `"anthropic"` resolves to (`anthropic_provider.AnthropicProvider`,
itself a thin binding of `RESUME_AI_MODEL` and friends onto the shared adapter).
`app/services/job/providers/base.py` is the same shape, bound to `JOB_AI_*`.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.ai.providers.base import (
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
        from app.services.resume.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    return resolve_provider(name or settings.RESUME_AI_PROVIDER, anthropic_factory=_anthropic)
