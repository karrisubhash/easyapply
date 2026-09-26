"""Which job-processing failures are worth trying again.

The mirror of the classification tests in `test_resume_provider.py`, plus the
job domain's own smaller vocabulary. The AI-provider half of this is decided
once in `app/services/ai/failures.py` and shared with the resume domain — see
`test_resume_provider.py::test_the_two_output_failures_are_classified_differently`
for the same assertion made from that side.
"""

from app.services.job.failures import classification, is_retryable


def test_provider_outages_are_retryable():
    for code in ("ai_provider_unavailable", "ai_timeout", "ai_rate_limited", "ai_provider_error"):
        assert is_retryable(code), code
        assert classification(code) == "transient"


def test_a_truncated_response_is_retryable_a_wrong_shape_is_not():
    """Sampling makes a truncated retry genuinely different; a schema mismatch
    twice in a row is a prompt problem, not bad luck."""
    assert is_retryable("ai_output_malformed")
    assert not is_retryable("ai_output_invalid")


def test_configuration_and_policy_failures_are_permanent():
    for code in ("ai_not_configured", "ai_unauthorized", "ai_request_invalid", "ai_content_rejected"):
        assert not is_retryable(code), code
        assert classification(code) == "permanent"


def test_an_unclassified_code_defaults_to_permanent_not_transient():
    """The deliberate direction to be wrong in: a new failure mode retried
    forever is worse than one that surfaces and gets classified."""
    assert not is_retryable("something_nobody_has_seen_yet")
    assert classification("something_nobody_has_seen_yet") == "unclassified"


def test_unexpected_error_is_retryable_infrastructure():
    assert is_retryable("unexpected_error")
    assert classification("unexpected_error") == "transient"


def test_structuring_failed_is_permanent():
    assert not is_retryable("structuring_failed")
    assert classification("structuring_failed") == "permanent"


def test_none_is_not_retryable():
    assert not is_retryable(None)
    assert classification(None) == "unclassified"
