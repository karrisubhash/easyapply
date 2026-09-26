"""The tailoring domain's binding of the shared Anthropic adapter.

All vendor logic — HTTP status mapping, refusal handling, structured-output
validation — lives in `app/services/ai/providers/anthropic.py` and is shared
with the resume and job domains' own bindings. This subclass exists only to
resolve `TAILORING_AI_MODEL`, `TAILORING_AI_TIMEOUT_SECONDS` and friends into
the constructor arguments the shared adapter takes.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.ai.providers.anthropic import AnthropicProvider as _AnthropicProvider


class AnthropicProvider(_AnthropicProvider):
    def __init__(self, client: Any = None, model: str | None = None) -> None:
        super().__init__(
            client=client,
            model=model or settings.TAILORING_AI_MODEL,
            timeout_seconds=settings.TAILORING_AI_TIMEOUT_SECONDS,
            max_retries=settings.TAILORING_AI_MAX_RETRIES,
            max_tokens=settings.TAILORING_AI_MAX_TOKENS,
            thinking=settings.TAILORING_AI_THINKING,
        )
