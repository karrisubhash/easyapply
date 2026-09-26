"""Provider adapters for structured model output, bound to the tailoring domain.

The application depends on `StructuredOutputProvider`; only the modules in this
package and `app/services/ai/providers/` know that a particular vendor exists.
The mirror of `app/services/job/providers/__init__.py`.
"""

from app.services.tailoring.providers.base import (
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
    get_provider,
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
