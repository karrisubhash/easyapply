"""The tailoring domain's binding of the shared OpenRouter adapter.

All vendor logic lives in `app/services/ai/providers/openrouter.py`. This
subclass resolves `TAILORING_AI_*` into the constructor arguments — and refuses
to run on the Claude default model id, which is not an OpenRouter model: with
`TAILORING_AI_PROVIDER=openrouter`, `TAILORING_AI_MODEL` must be set explicitly.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.ai.providers.openrouter import OpenRouterProvider as _OpenRouterProvider


class OpenRouterProvider(_OpenRouterProvider):
    def __init__(
        self, client: Any = None, model: str | None = None, max_retries: int | None = None
    ) -> None:
        super().__init__(
            client=client,
            model=model or _explicit_model(),
            timeout_seconds=settings.TAILORING_AI_TIMEOUT_SECONDS,
            max_retries=(
                settings.TAILORING_AI_MAX_RETRIES if max_retries is None else max_retries
            ),
            max_tokens=settings.TAILORING_AI_MAX_TOKENS,
        )


def _explicit_model() -> str | None:
    """`TAILORING_AI_MODEL` only if configuration actually set it.

    None otherwise, which the adapter reports as `ai_not_configured` on the first
    call — rather than sending "claude-opus-5" to a service that does not have it.
    """
    if "TAILORING_AI_MODEL" not in settings.model_fields_set:
        return None
    return (settings.TAILORING_AI_MODEL or "").strip() or None
