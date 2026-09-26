"""The resume domain's binding of the shared Anthropic adapter.

All vendor logic — HTTP status mapping, refusal handling, structured-output
validation — lives in `app/services/ai/providers/anthropic.py` and is shared
with the job domain's own binding
(`app/services/job/providers/anthropic_provider.py`). This subclass exists only
to resolve `RESUME_AI_MODEL`, `RESUME_AI_TIMEOUT_SECONDS` and friends into the
constructor arguments the shared adapter takes, so resume and job structuring
can each be tuned independently while running the same request/response code.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.ai.providers.anthropic import AnthropicProvider as _AnthropicProvider


class AnthropicProvider(_AnthropicProvider):
    def __init__(self, client: Any = None, model: str | None = None) -> None:
        super().__init__(
            client=client,
            model=model or settings.RESUME_AI_MODEL,
            timeout_seconds=settings.RESUME_AI_TIMEOUT_SECONDS,
            max_retries=settings.RESUME_AI_MAX_RETRIES,
            max_tokens=settings.RESUME_AI_MAX_TOKENS,
            thinking=settings.RESUME_AI_THINKING,
        )
