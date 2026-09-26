"""The Anthropic adapter.

The only module in the application that imports the Anthropic SDK. Everything
it knows about the caller's domain arrives as two strings and a Pydantic
model; everything it returns is a validated instance of that model or a
ProviderError with a code from base.py. It does not read `RESUME_AI_*` or
`JOB_AI_*` settings itself — each domain's own thin subclass
(`app/services/resume/providers/anthropic_provider.py`,
`app/services/job/providers/anthropic_provider.py`) resolves its own
configuration and passes it in, which is what lets two domains tune model,
timeout and token limits independently while sharing every line of vendor
translation below.

Two decisions worth naming:

Structured output is a property of the request, not of the parsing. The model
is passed as `output_format`, which the SDK turns into both the JSON schema it
sends and the validator it runs on the reply — so "the model returned prose" is
an exception at this boundary rather than a surprise three layers downstream.

No sampling parameters are exposed. The Claude models this adapter targets
reject `temperature`, `top_p` and `top_k` outright, so a configurable
temperature would be a configurable 400. Depth is controlled by adaptive
thinking instead, which is the knob these models actually have.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.services.ai.providers.base import (
    PROVIDER_CONTENT_REJECTED,
    PROVIDER_ERROR,
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
)

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """Claude, constrained to a caller-supplied schema.

    Every knob is a constructor argument rather than a module-level setting:
    this class has no opinion about which domain is calling it, only about how
    to talk to the vendor once it has been told what to ask for.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        client: Any = None,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        max_tokens: int,
        thinking: bool = True,
    ) -> None:
        self.model = model
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._thinking = thinking

    # --- client ---------------------------------------------------------------

    def _get_client(self):
        """Build the client on first use.

        Lazily, because constructing it reads the key: a deployment with AI
        switched off should never touch that configuration at all.
        """
        if self._client is not None:
            return self._client

        import anthropic

        if not settings.ANTHROPIC_API_KEY:
            raise ProviderError(
                PROVIDER_NOT_CONFIGURED,
                "AI structuring is enabled but no API key is configured.",
            )

        self._client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        return self._client

    # --- the call -------------------------------------------------------------

    def complete(self, *, system: str, user: str, output_model) -> StructuredCompletion:
        import anthropic

        client = self._get_client()

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": output_model,
        }
        if self._thinking:
            request["thinking"] = {"type": "adaptive"}

        started = time.perf_counter()
        try:
            response = client.messages.parse(**request)
        except anthropic.APITimeoutError as exc:
            raise ProviderError(
                PROVIDER_TIMEOUT, "The AI service did not answer in time."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                PROVIDER_UNAVAILABLE, "The AI service could not be reached."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise _from_status(exc) from exc
        except anthropic.APIResponseValidationError as exc:
            raise ProviderError(
                PROVIDER_OUTPUT_MALFORMED,
                "The AI service returned a response we could not read.",
            ) from exc
        except ValidationError as exc:
            # parse() validates the reply against the schema and raises here
            # rather than handing back an empty result, so this is where output
            # failures are classified — and the two kinds are genuinely
            # different: see _from_validation_error.
            raise _from_validation_error(exc) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # A policy decline arrives as a successful HTTP response, so it has to
        # be checked rather than caught.
        if getattr(response, "stop_reason", None) == "refusal":
            raise ProviderError(
                PROVIDER_CONTENT_REJECTED,
                "The AI service declined to process this request.",
            )

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            # Nearly always max_tokens: the model was still writing JSON when it
            # was cut off, so there is no complete object to validate.
            raise ProviderError(
                PROVIDER_OUTPUT_MALFORMED,
                "The AI response did not contain a complete structured result.",
            )

        if not isinstance(parsed, output_model):
            try:
                parsed = output_model.model_validate(parsed)
            except ValidationError as exc:
                raise ProviderError(
                    PROVIDER_SCHEMA_INVALID,
                    "The AI response did not match the expected schema.",
                ) from exc

        return StructuredCompletion(
            output=parsed,
            metadata=_metadata(response, elapsed_ms=elapsed_ms, model=self.model),
        )


def _from_validation_error(exc: ValidationError) -> ProviderError:
    """Tell "the model could not produce JSON" from "it produced the wrong JSON".

    They deserve different answers. Output that is not parseable is usually a
    truncated generation, and sampling makes the next attempt genuinely
    different — worth a retry. Output that parses but does not fit the schema is
    the model disagreeing with the schema, which the next attempt will do again;
    that is a prompt or schema problem for a person to look at.

    Pydantic reports the first case as `json_invalid`, which is what makes the
    distinction available without inspecting the text — and the text is the
    caller's document, so not inspecting it is the point.
    """
    if any(error.get("type") == "json_invalid" for error in exc.errors()):
        return ProviderError(
            PROVIDER_OUTPUT_MALFORMED,
            "The AI response was not complete, readable output.",
        )
    return ProviderError(
        PROVIDER_SCHEMA_INVALID,
        "The AI response did not match the expected schema.",
    )


def _from_status(exc) -> ProviderError:
    """Translate an HTTP failure into this domain's vocabulary.

    On status code, never on message text: the text is vendor prose that
    changes without notice, and matching on it is how error handling silently
    stops working after an SDK upgrade.
    """
    import anthropic

    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(
            PROVIDER_RATE_LIMITED, "The AI service is rate limiting this workload."
        )
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderError(
            PROVIDER_UNAUTHORIZED, "The AI service rejected our credentials."
        )

    status = getattr(exc, "status_code", None)
    if status is not None and 500 <= status < 600:
        return ProviderError(PROVIDER_ERROR, "The AI service failed to answer.")
    if status is not None and 400 <= status < 500:
        # Our request was wrong — a model name that does not exist, a schema the
        # service will not accept. Retrying sends the same wrong request.
        return ProviderError(
            PROVIDER_INVALID_REQUEST, "The AI service rejected the request."
        )
    return ProviderError(PROVIDER_ERROR, "The AI service failed to answer.")


def _metadata(response, *, elapsed_ms: int, model: str) -> dict[str, Any]:
    """Counts, identifiers and timings. Never content.

    This is written to the database and read by whoever audits a result later,
    so everything in it is a number, an id or an enum — nothing derived from
    the request, and nothing from the credentials.
    """
    usage = getattr(response, "usage", None)
    metadata: dict[str, Any] = {
        "latency_ms": elapsed_ms,
        "model": getattr(response, "model", model),
        "stop_reason": getattr(response, "stop_reason", None),
        "response_id": getattr(response, "id", None),
    }
    if usage is not None:
        for attribute in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = getattr(usage, attribute, None)
            if value is not None:
                metadata[attribute] = value
    return {key: value for key, value in metadata.items() if value is not None}
