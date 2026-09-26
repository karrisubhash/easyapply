"""Which AI-provider failures are worth trying again.

The vocabulary in `app/services/ai/providers/base.py` is vendor-facing, not
domain-facing: an adapter raises `ai_timeout` whether it was called for a
resume or a job posting, because the failure is genuinely the same fact about
the same vendor. This module is where that vocabulary is classified once,
shared by `app/services/resume/failures.py` and `app/services/job/failures.py`
so the two domains cannot quietly disagree about whether a rate limit is worth
retrying.

Each domain still owns its own non-AI failure codes (a malformed PDF, a bad
heading pattern) and its own `is_retryable`/`classification` — this module only
supplies the AI half of that union. See either domain's `failures.py` for the
full picture and for why an unrecognised code defaults to permanent.
"""

#: The provider was not able to answer at all. Retrying is the whole fix.
AI_TRANSIENT_CODES = frozenset(
    {
        "ai_provider_unavailable",
        "ai_timeout",
        "ai_rate_limited",
        # An overloaded or 5xx provider. A 4xx is ai_request_invalid and is not
        # in this set.
        "ai_provider_error",
        # The model returned something that was not the schema. Sampling makes
        # the next attempt genuinely different, so one more is worth it — and
        # the domain's own attempt cap (or a person clicking retry) is what
        # stops it becoming a loop.
        "ai_output_malformed",
    }
)

#: The provider answered, and answering again would not help.
AI_PERMANENT_CODES = frozenset(
    {
        # Configuration, not an outage — a key or model that does not exist
        # will not exist on the next attempt either.
        "ai_not_configured",
        "ai_unauthorized",
        "ai_authentication_failed",
        # No credit on the account. A person has to act before the next attempt
        # can differ from this one.
        "ai_billing_blocked",
        # Our request was malformed in a way retrying resends unchanged.
        "ai_request_invalid",
        # A policy decline. The same input will be declined again.
        "ai_content_rejected",
        # Valid JSON, wrong shape, twice in a row is a prompt or schema problem
        # rather than bad luck. Distinguished from ai_output_malformed, which is
        # the provider failing to produce the schema at all.
        "ai_output_invalid",
    }
)
