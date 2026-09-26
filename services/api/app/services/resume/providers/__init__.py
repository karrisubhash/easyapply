"""Provider adapters for structured model output.

The application depends on `StructuredOutputProvider`; only the modules in this
package know that a particular vendor exists.
"""

from app.services.resume.providers.base import (
    PROVIDER_ERROR_CODES,
    ProviderError,
    StructuredCompletion,
    StructuredOutputProvider,
    get_provider,
)

__all__ = [
    "PROVIDER_ERROR_CODES",
    "ProviderError",
    "StructuredCompletion",
    "StructuredOutputProvider",
    "get_provider",
]
