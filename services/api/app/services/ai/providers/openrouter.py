"""The OpenRouter adapter.

A second vendor behind the same boundary as `anthropic.py`: two strings and a
Pydantic model in, a validated instance of that model or a `ProviderError` out.
It speaks OpenRouter's OpenAI-compatible chat-completions API over plain HTTP
(`httpx`), so no second vendor SDK is needed. Like the Anthropic adapter it reads
no domain settings itself — `app/services/tailoring/providers/openrouter_provider.py`
resolves `TAILORING_AI_*` into the constructor arguments.

Three decisions worth naming:

Structured output is a property of the request. The model's JSON Schema is sent
as `response_format: json_schema` with `strict: true`, and `provider.require_parameters`
tells OpenRouter to route only to upstream endpoints that honour that parameter —
without it, OpenRouter may silently pick an endpoint that ignores the schema and
returns prose. The reply is then validated by Pydantic regardless: the schema
sent is what the model is steered by, the Pydantic model is what decides.

Strict mode requires every property to be listed as required and every object to
forbid extra keys, which Pydantic's own schema does not do for fields that have
defaults. `strict_json_schema` closes that gap without changing what validates:
an optional field becomes "required, but may be null", and the Pydantic model
accepts an explicit null exactly as it accepts an omitted one.

Errors are classified on HTTP status and OpenRouter's numeric error code, never
on message text, and the vendor's text never leaves this module — it can quote
the request, and the request is the caller's document.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.services.ai.providers.base import (
    PROVIDER_AUTHENTICATION_FAILED,
    PROVIDER_BILLING_BLOCKED,
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

#: Statuses worth sending the same request again for: the provider's minute, not
#: our request. Everything else in 4xx is final.
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
#: Never wait longer than this between attempts, whatever Retry-After says. A
#: daily free-tier cap answers 429 with a Retry-After of hours.
_MAX_BACKOFF_SECONDS = 30.0


class OpenRouterProvider:
    """Any OpenRouter-hosted model, constrained to a caller-supplied schema."""

    name = "openrouter"

    def __init__(
        self,
        *,
        client: Any = None,
        model: str | None,
        timeout_seconds: float,
        max_retries: int,
        max_tokens: int,
        base_url: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._max_tokens = max_tokens
        self._base_url = (base_url or settings.OPENROUTER_BASE_URL).rstrip("/")
        self._sleep = sleep

    # --- client ---------------------------------------------------------------

    def _get_client(self):
        """Build the client on first use, reading the key only then."""
        if self._client is not None:
            return self._client

        import httpx

        if not settings.OPENROUTER_API_KEY:
            raise ProviderError(
                PROVIDER_NOT_CONFIGURED,
                "AI generation is set to use OpenRouter but no API key is configured.",
            )

        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "X-Title": "EasyApply",
            },
        )
        return self._client

    # --- the call -------------------------------------------------------------

    def complete(self, *, system: str, user: str, output_model) -> StructuredCompletion:
        if not self.model:
            raise ProviderError(
                PROVIDER_NOT_CONFIGURED,
                "AI generation is set to use OpenRouter but no model is configured.",
            )

        client = self._get_client()
        request = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "strict": True,
                    "schema": strict_json_schema(output_model),
                },
            },
            # Only endpoints that implement every parameter above. This is what
            # makes `response_format` a requirement rather than a suggestion.
            "provider": {"require_parameters": True},
        }

        started = time.perf_counter()
        body, attempts = self._post_with_retries(client, request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        parsed = _parse_choice(body, output_model)
        return StructuredCompletion(
            output=parsed,
            metadata=_metadata(body, elapsed_ms=elapsed_ms, model=self.model, attempts=attempts),
        )

    def _post_with_retries(self, client, request: dict[str, Any]) -> tuple[dict[str, Any], int]:
        import httpx

        attempt = 0
        while True:
            attempt += 1
            retry_after: float | None = None
            try:
                response = client.post("/chat/completions", json=request)
            except httpx.TimeoutException as exc:
                error = ProviderError(PROVIDER_TIMEOUT, "The AI service did not answer in time.")
                cause: BaseException = exc
            except httpx.TransportError as exc:
                error = ProviderError(PROVIDER_UNAVAILABLE, "The AI service could not be reached.")
                cause = exc
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            PROVIDER_OUTPUT_MALFORMED,
                            "The AI service returned a response we could not read.",
                        ) from exc
                    # OpenRouter can report an upstream failure inside a 200.
                    if isinstance(body, dict) and isinstance(body.get("error"), dict):
                        raise _from_error_object(body["error"], status=None)
                    if not isinstance(body, dict):
                        raise ProviderError(
                            PROVIDER_OUTPUT_MALFORMED,
                            "The AI service returned a response we could not read.",
                        )
                    return body, attempt

                logger.warning(
                    "openrouter.http_error",
                    extra={"status": response.status_code, "attempt": attempt},
                )
                error = _from_status(response)
                cause = None
                if response.status_code not in _RETRYABLE_STATUSES:
                    raise error
                retry_after = _retry_after(response)

            if attempt > self._max_retries:
                if cause is not None:
                    raise error from cause
                raise error

            backoff = retry_after if retry_after is not None else 2.0 ** (attempt - 1)
            self._sleep(min(backoff, _MAX_BACKOFF_SECONDS))


# --- the schema ------------------------------------------------------------------


def strict_json_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic's JSON Schema, reshaped for `strict: true` structured output.

    Every object lists all of its properties as required and forbids extras, and
    `default` is dropped (strict mode does not accept it). Walks only the schema
    keywords that hold subschemas, so a property that happens to be *named*
    `default` or `required` is left alone.
    """
    schema = copy.deepcopy(output_model.model_json_schema())
    _strictify(schema)
    return schema


def _strictify(node: Any) -> None:
    if not isinstance(node, dict):
        return
    node.pop("default", None)

    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = list(properties)
        node["additionalProperties"] = False
        for child in properties.values():
            _strictify(child)

    for key in ("$defs", "definitions"):
        if isinstance(node.get(key), dict):
            for child in node[key].values():
                _strictify(child)

    for key in ("items", "additionalProperties", "not"):
        if isinstance(node.get(key), dict):
            _strictify(node[key])

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(node.get(key), list):
            for child in node[key]:
                _strictify(child)


# --- the reply -------------------------------------------------------------------


def _parse_choice(body: dict[str, Any], output_model):
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError(
            PROVIDER_OUTPUT_MALFORMED,
            "The AI response did not contain a complete structured result.",
        )
    choice = choices[0]

    if isinstance(choice.get("error"), dict):
        raise _from_error_object(choice["error"], status=None)

    finish_reason = choice.get("finish_reason")
    message = choice.get("message") or {}

    # A policy decline arrives as a successful response, so it is checked, not caught.
    if message.get("refusal") or finish_reason == "content_filter":
        raise ProviderError(
            PROVIDER_CONTENT_REJECTED,
            "The AI service declined to process this request.",
        )
    if finish_reason == "error":
        raise ProviderError(PROVIDER_ERROR, "The AI service failed to answer.")

    content = _content_text(message.get("content"))
    if not content:
        # Nearly always the token ceiling: reasoning or JSON cut off mid-stream.
        raise ProviderError(
            PROVIDER_OUTPUT_MALFORMED,
            "The AI response did not contain a complete structured result.",
        )

    try:
        return output_model.model_validate_json(_unfence(content))
    except ValidationError as exc:
        raise _from_validation_error(exc) from exc


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        # Some upstreams return content parts rather than a string.
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return ""


def _unfence(text: str) -> str:
    """Remove one Markdown code fence around the whole reply, if present.

    Some upstream models wrap schema-constrained JSON in ```json ... ``` even so.
    Only a fence enclosing the entire reply is removed; nothing inside is touched.
    """
    if text.startswith("```") and text.endswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            return text[first_newline + 1 : -3].strip()
    return text


def _from_validation_error(exc: ValidationError) -> ProviderError:
    """Unreadable output (retry may help) versus the wrong shape (it will not).

    The same split `anthropic.py` makes, for the same reason: Pydantic reports
    unparseable input as `json_invalid`, which classifies without reading text.
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


# --- errors ----------------------------------------------------------------------


def _from_status(response) -> ProviderError:
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    return _from_error_object(error if isinstance(error, dict) else {}, status=response.status_code)


def _from_error_object(error: dict[str, Any], *, status: int | None) -> ProviderError:
    """Translate OpenRouter's status / numeric error code into our vocabulary.

    OpenRouter's documented codes: 400 bad request, 401 invalid credentials,
    402 insufficient credits, 403 input flagged by moderation, 408 timeout,
    429 rate limited, 502 upstream model failed, 503 no endpoint available.
    """
    code = error.get("code")
    if not isinstance(code, int):
        code = status

    if code == 401:
        return ProviderError(
            PROVIDER_AUTHENTICATION_FAILED, "The AI service rejected our credentials."
        )
    if code == 402:
        return ProviderError(
            PROVIDER_BILLING_BLOCKED, "The AI service account has insufficient credit."
        )
    if code == 403:
        metadata = error.get("metadata")
        if isinstance(metadata, dict) and ("reasons" in metadata or "flagged_input" in metadata):
            return ProviderError(
                PROVIDER_CONTENT_REJECTED,
                "The AI service declined to process this request.",
            )
        return ProviderError(
            PROVIDER_UNAUTHORIZED, "The AI service refused access to this model."
        )
    if code == 408:
        return ProviderError(PROVIDER_TIMEOUT, "The AI service did not answer in time.")
    if code == 429:
        return ProviderError(
            PROVIDER_RATE_LIMITED, "The AI service is rate limiting this workload."
        )
    if code in (502, 503):
        # Upstream down, or no endpoint currently able to serve the request.
        return ProviderError(PROVIDER_UNAVAILABLE, "The AI service could not be reached.")
    if isinstance(code, int) and 500 <= code < 600:
        return ProviderError(PROVIDER_ERROR, "The AI service failed to answer.")
    if isinstance(code, int) and 400 <= code < 500:
        # A model id that does not exist, a schema no endpoint will accept, no
        # endpoint supporting the required parameters. Retrying resends it.
        return ProviderError(PROVIDER_INVALID_REQUEST, "The AI service rejected the request.")
    return ProviderError(PROVIDER_ERROR, "The AI service failed to answer.")


def _retry_after(response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


# --- metadata --------------------------------------------------------------------


def _metadata(
    body: dict[str, Any], *, elapsed_ms: int, model: str, attempts: int
) -> dict[str, Any]:
    """Counts, identifiers and timings. Never content, never credentials."""
    choices = body.get("choices") or [{}]
    first = choices[0] if isinstance(choices[0], dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details")
    metadata: dict[str, Any] = {
        "latency_ms": elapsed_ms,
        "attempts": attempts,
        "model": body.get("model", model),
        "upstream_provider": body.get("provider"),
        "stop_reason": first.get("finish_reason"),
        "response_id": body.get("id"),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
        "cost": usage.get("cost"),
    }
    return {
        key: value
        for key, value in metadata.items()
        if isinstance(value, (int, float, str)) and not isinstance(value, bool)
    }


__all__ = ["OpenRouterProvider", "strict_json_schema"]
