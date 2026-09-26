"""The provider boundary.

A provider takes a prompt and a Pydantic model, and returns an instance of that
model. It does not know what a resume is, what a job posting is, or that
anything downstream will review its output. That narrowness is the point: the
application depends on this protocol, so swapping vendors is a new class in
this package and a configuration change — and reusing it across domains is a
new settings namespace, not a new copy of the HTTP handling.

The other half of the contract is the error vocabulary. A vendor SDK raises its
own exception types, and letting those travel would put vendor names in the
pipeline's except clauses and in the database's error_code column. Adapters
translate to `ProviderError` with a code from this module, and those codes are
what each domain's own `failures.py` classifies as retryable or not.

Free-form output is not a mode this boundary offers. `complete()` takes the
model the response must satisfy and returns a validated instance or raises;
there is no path that hands unparsed text to a caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

# --- the error vocabulary ----------------------------------------------------
#
# Every adapter maps into exactly these. Anything it cannot map becomes
# PROVIDER_ERROR, which is transient — an unclassified vendor exception is more
# often a bad minute than a bad request, and the attempt cap bounds the cost of
# being wrong about that.

#: The provider could not be reached at all — DNS, connection, TLS.
PROVIDER_UNAVAILABLE = "ai_provider_unavailable"
#: The call exceeded its wall clock.
PROVIDER_TIMEOUT = "ai_timeout"
#: Quota or concurrency limit.
PROVIDER_RATE_LIMITED = "ai_rate_limited"
#: The provider answered with a server-side failure.
PROVIDER_ERROR = "ai_provider_error"
#: We sent something the provider rejected. Our bug, not its outage.
PROVIDER_INVALID_REQUEST = "ai_request_invalid"
#: Credentials missing, wrong, or without access to the model.
PROVIDER_UNAUTHORIZED = "ai_unauthorized"
#: The provider said the key itself is not valid — revoked, mistyped, disabled.
#: Narrower than PROVIDER_UNAUTHORIZED, for adapters whose vendor distinguishes
#: "who are you" from "you may not use this model".
PROVIDER_AUTHENTICATION_FAILED = "ai_authentication_failed"
#: The account has no credit or no plan. Not an outage and not our request: a
#: person has to pay before the same call can succeed.
PROVIDER_BILLING_BLOCKED = "ai_billing_blocked"
#: Configured to use a provider that has not been given what it needs to run.
PROVIDER_NOT_CONFIGURED = "ai_not_configured"
#: The response was not the requested schema — truncated, or not JSON at all.
PROVIDER_OUTPUT_MALFORMED = "ai_output_malformed"
#: Parsed, but failed validation against the model.
PROVIDER_SCHEMA_INVALID = "ai_output_invalid"
#: The provider declined the input or the output on policy grounds.
PROVIDER_CONTENT_REJECTED = "ai_content_rejected"

PROVIDER_ERROR_CODES = frozenset(
    {
        PROVIDER_UNAVAILABLE,
        PROVIDER_TIMEOUT,
        PROVIDER_RATE_LIMITED,
        PROVIDER_ERROR,
        PROVIDER_INVALID_REQUEST,
        PROVIDER_UNAUTHORIZED,
        PROVIDER_AUTHENTICATION_FAILED,
        PROVIDER_BILLING_BLOCKED,
        PROVIDER_NOT_CONFIGURED,
        PROVIDER_OUTPUT_MALFORMED,
        PROVIDER_SCHEMA_INVALID,
        PROVIDER_CONTENT_REJECTED,
    }
)


class ProviderError(RuntimeError):
    """A provider call that did not produce a validated result.

    The message is ours and is safe to store: adapters never pass a vendor
    exception's text through, because those quote the request — and the
    request is the caller's document, whatever domain it belongs to.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StructuredCompletion:
    """A validated result, plus what it cost and what produced it.

    `metadata` is counts, identifiers and timings only. It is written to the
    database and read back by anyone auditing a result, so nothing in it may be
    derived from the request's text or contain a credential.
    """

    output: BaseModel
    metadata: dict[str, Any] = field(default_factory=dict)


class StructuredOutputProvider(Protocol):
    """What a structuring layer is allowed to know about a model vendor."""

    #: Stable identifier written to the domain's own `..._structured_results`
    #: or `..._requirements` table, under its `provider` column.
    name: str
    #: The model actually used, as the vendor names it.
    model: str | None

    def complete(
        self,
        *,
        system: str,
        user: str,
        output_model: type[ModelT],
    ) -> StructuredCompletion: ...


class UnconfiguredProvider:
    """Stands in when AI is switched off, and says so if called.

    It exists so a structuring layer always has a provider object and never
    branches on None. Nothing calls it in the default configuration: with AI
    disabled, the deterministic structurer runs instead.
    """

    name = "unconfigured"
    model = None

    def complete(self, *, system, user, output_model) -> StructuredCompletion:
        raise ProviderError(
            PROVIDER_NOT_CONFIGURED,
            "AI structuring is enabled but no provider is configured.",
        )


def resolve_provider(
    selected: str | None,
    *,
    anthropic_factory: Callable[[], StructuredOutputProvider],
    openrouter_factory: Callable[[], StructuredOutputProvider] | None = None,
) -> StructuredOutputProvider:
    """The selection rule every domain's `get_provider()` shares.

    Each domain reads its own settings key (`RESUME_AI_PROVIDER`,
    `JOB_AI_PROVIDER`, ...) and its own constructor (which in turn reads that
    domain's own model/timeout/token settings) — this function only owns the
    name-to-adapter mapping, which is genuinely identical across domains.

    `openrouter_factory` is optional because only the domains that pass one can
    be pointed at OpenRouter; for the rest, "openrouter" stays an unregistered
    name and is refused like any other.
    """
    normalised = (selected or "").strip().lower()

    if normalised == "anthropic":
        return anthropic_factory()

    if normalised == "openrouter" and openrouter_factory is not None:
        return openrouter_factory()

    if normalised in ("", "none", "disabled"):
        return UnconfiguredProvider()

    raise ProviderError(
        PROVIDER_NOT_CONFIGURED, f"No provider adapter is registered for {normalised!r}."
    )
