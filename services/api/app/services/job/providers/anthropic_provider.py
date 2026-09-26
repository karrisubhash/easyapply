"""The job domain's binding of the shared Anthropic adapter.

All vendor logic — HTTP status mapping, refusal handling, structured-output
validation — lives in `app/services/ai/providers/anthropic.py` and is shared
with the resume domain's own binding
(`app/services/resume/providers/anthropic_provider.py`). This subclass exists
only to resolve `JOB_AI_MODEL`, `JOB_AI_TIMEOUT_SECONDS` and friends into the
constructor arguments the shared adapter takes.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.ai.providers.anthropic import AnthropicProvider as _AnthropicProvider


class AnthropicProvider(_AnthropicProvider):
    def __init__(self, client: Any = None, model: str | None = None) -> None:
        super().__init__(
            client=client,
            model=model or settings.JOB_AI_MODEL,
            timeout_seconds=settings.JOB_AI_TIMEOUT_SECONDS,
            max_retries=settings.JOB_AI_MAX_RETRIES,
            max_tokens=settings.JOB_AI_MAX_TOKENS,
            thinking=settings.JOB_AI_THINKING,
        )
