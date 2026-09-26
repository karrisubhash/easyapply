"""Which failures are worth trying again.

The mirror of `app/services/resume/failures.py`, sized down: a job posting has
no file to be corrupt and no OCR engine to be missing, so the only failures
this domain can produce are an AI provider having a bad call
(`app/services/ai/failures.py`, shared with the resume domain so the two cannot
quietly disagree) and the small set of infrastructure/adapter failures below.

  * TRANSIENT — the posting is fine and something around it was not. A
    provider that was unreachable or rate limited, an exception a stage did
    not anticipate. Running it again is the whole fix.

  * PERMANENT — the posting, or our reading of it, is the problem: the
    provider refused the request, or a structurer raised something that was
    not a recognised failure at all. The same description through the same
    code produces the same failure.

An unrecognised code is treated as PERMANENT — the same deliberate direction
`resume.failures` argues for: a failure mode nobody has classified yet should
surface rather than loop.
"""

from app.services.ai.failures import AI_PERMANENT_CODES, AI_TRANSIENT_CODES

#: A stage raised something not modelled as a `StructuringError` or
#: `ProviderError` at all. Usually a database or network blip; bounded by the
#: caller's own attempt discipline if it is not.
_TRANSIENT_INFRASTRUCTURE = frozenset({"unexpected_error"})

TRANSIENT_ERROR_CODES = _TRANSIENT_INFRASTRUCTURE | AI_TRANSIENT_CODES

#: Named rather than merely "everything else", so that adding a failure code
#: without classifying it is visible in review.
PERMANENT_ERROR_CODES = (
    frozenset(
        {
            # A structurer raised something its own adapter could not classify.
            "structuring_failed",
        }
    )
    | AI_PERMANENT_CODES
)


#: PERMANENT failures caused by how THIS deployment is set up rather than by
#: the posting or by our reading of it. Nothing about the job's text changes
#: between attempts, but the deployment can: an operator adds the missing key,
#: tops up the account, fixes the credentials, or switches `JOB_AI_ENABLED`
#: off, and the identical reprocess then succeeds. The server cannot see
#: whether that has happened, so it must not refuse the attempt that would
#: find out — which `require_retryable` used to do, leaving a job that failed
#: on `ai_billing_blocked` unreprocessable forever, whatever was fixed since.
#:
#: The same four codes `tailoring.pipeline._provider_message` already groups as
#: "not configured on this deployment". Still PERMANENT for `classification`
#: and never retried automatically (`is_retryable` is unchanged): only a
#: person asking is allowed to try again, and a retry under unchanged
#: configuration fails fast with the same code.
#:
#: Deliberately absent: `ai_content_rejected` (a fact about this posting),
#: `ai_request_invalid` and `ai_output_invalid` (our request or prompt — a
#: code change, and a deploy that makes one opens a new attempt anyway), and
#: `structuring_failed`.
CONFIGURATION_ERROR_CODES = frozenset(
    {
        "ai_not_configured",
        "ai_unauthorized",
        "ai_authentication_failed",
        "ai_billing_blocked",
    }
)
assert CONFIGURATION_ERROR_CODES <= PERMANENT_ERROR_CODES


def is_retryable(error_code: str | None) -> bool:
    """Whether another attempt could plausibly succeed ON ITS OWN — the rule for
    automatic retries and for logging."""
    return error_code in TRANSIENT_ERROR_CODES


def is_user_retryable(error_code: str | None) -> bool:
    """Whether a PERSON asking for another attempt should be allowed one:
    every transient failure, plus the configuration failures a change to the
    deployment can cure. What `require_retryable` enforces and `can_retry`
    reports, so the button and the endpoint cannot disagree."""
    return is_retryable(error_code) or error_code in CONFIGURATION_ERROR_CODES


def classification(error_code: str | None) -> str:
    """The label used in events and structured logs. Never the message."""
    if error_code in TRANSIENT_ERROR_CODES:
        return "transient"
    if error_code in PERMANENT_ERROR_CODES:
        return "permanent"
    return "unclassified"
