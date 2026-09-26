"""The AI provider boundary.

Nothing here reaches the network. The Anthropic SDK is exercised against a mock
transport, which is the only way to assert what we actually send and how we
translate what comes back — the two things that matter and that a stubbed client
would let us skip.
"""

import json

import httpx2 as httpx
import pytest

from app.core.config import settings
from app.schemas.resume_candidate import CANDIDATE_SCHEMA_VERSION, ResumeCandidate
from app.services.resume.extraction import ExtractedDocument, ExtractedPage
from app.services.resume.providers import ProviderError, get_provider
from app.services.resume.providers.anthropic_provider import AnthropicProvider
from app.services.resume.providers.base import UnconfiguredProvider
from app.services.resume.structuring import (
    DeterministicResumeStructurer,
    ProviderResumeStructurer,
    get_structurer,
)

CANDIDATE = {
    "experiences": [
        {
            "company": "Acme",
            "job_title": "Engineer",
            "provenance": [
                {"page": 1, "quote": "Engineer, Acme", "confidence": 0.9}
            ],
        }
    ],
    "education": [],
    "skills": [],
    "projects": [],
    "certifications": [],
}


@pytest.fixture
def document():
    return ExtractedDocument(
        pages=[ExtractedPage(page_number=1, text="Engineer, Acme", method="text")]
    )


def build_provider(handler):
    """An AnthropicProvider whose transport is under the test's control."""
    import anthropic

    client = anthropic.Anthropic(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return AnthropicProvider(client=client)


def message(body, *, stop_reason="end_turn"):
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": body}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 340},
        },
    )


def refusal():
    """A policy decline: HTTP 200, no content, and a stop reason that says so."""
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [],
            "stop_reason": "refusal",
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 0},
        },
    )


# --- the happy path ----------------------------------------------------------


def test_the_adapter_returns_the_requested_model(document):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    completion = build_provider(handler).complete(
        system="rules", user="resume text", output_model=ResumeCandidate
    )

    assert isinstance(completion.output, ResumeCandidate)
    assert completion.output.experiences[0].company == "Acme"

    # The schema is part of the request: the model is constrained, not asked
    # politely for JSON and parsed hopefully.
    fmt = captured["body"]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "$defs" in fmt["schema"]


def test_no_sampling_parameters_are_sent(document):
    """The models this adapter targets reject them; offering the knob would be
    offering a 400."""
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    build_provider(handler).complete(
        system="rules", user="text", output_model=ResumeCandidate
    )

    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in captured["body"]


def test_metadata_is_counts_and_ids_but_never_content(document):
    def handler(request):
        return message(json.dumps(CANDIDATE))

    completion = build_provider(handler).complete(
        system="rules", user="SECRET RESUME TEXT", output_model=ResumeCandidate
    )

    assert completion.metadata["input_tokens"] == 120
    assert completion.metadata["output_tokens"] == 340
    assert completion.metadata["stop_reason"] == "end_turn"
    assert completion.metadata["latency_ms"] >= 0

    # Nothing derived from the document, and no credential.
    serialised = json.dumps(completion.metadata)
    assert "SECRET RESUME TEXT" not in serialised
    assert "test-key" not in serialised


# --- failure classification --------------------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [
        (429, "ai_rate_limited"),
        (401, "ai_unauthorized"),
        (403, "ai_unauthorized"),
        (400, "ai_request_invalid"),
        (404, "ai_request_invalid"),
        (500, "ai_provider_error"),
        (529, "ai_provider_error"),
    ],
)
def test_http_failures_map_to_the_domain_vocabulary(status, expected):
    """On status code, never on message text: vendor prose changes without
    notice, and matching on it is how error handling quietly stops working."""

    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == expected


def test_a_timeout_is_reported_as_a_timeout():
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_timeout"


def test_an_unreachable_provider_is_distinguished_from_a_rejection():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_provider_unavailable"


def test_output_that_is_not_json_is_malformed_not_schema_invalid():
    """Truncation and prose are a different problem from the wrong shape, and
    only one of them is worth another attempt."""

    def handler(request):
        return message('{"experiences": [ truncated mid')

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_output_malformed"


def test_valid_json_of_the_wrong_shape_fails_schema_validation():
    """Parseable but wrong is the model disagreeing with the schema, which the
    next attempt will do again — a different answer from a truncation."""

    def handler(request):
        return message(json.dumps({"experiences": "not a list"}))

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_output_invalid"


def test_the_two_output_failures_are_classified_differently():
    """One is worth another attempt and one is not; collapsing them would
    either retry a schema bug forever or give up on a truncation."""
    from app.services.resume.failures import is_retryable

    assert is_retryable("ai_output_malformed")
    assert not is_retryable("ai_output_invalid")


def test_a_policy_refusal_is_reported_rather_than_parsed():
    """A decline arrives as a 200, so it has to be checked, not caught."""

    def handler(request):
        return refusal()

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_content_rejected"


def test_a_missing_key_is_a_configuration_failure_not_an_outage(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    with pytest.raises(ProviderError) as raised:
        AnthropicProvider().complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_not_configured"


def test_a_provider_error_never_quotes_the_request():
    """Vendor exception text contains the prompt, and the prompt is the resume."""

    def handler(request):
        return httpx.Response(
            400, json={"error": {"message": "bad input: JANE DOE, 42 Acacia Avenue"}}
        )

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="JANE DOE, 42 Acacia Avenue", output_model=ResumeCandidate
        )
    assert "Acacia" not in raised.value.message
    assert "JANE DOE" not in raised.value.message


# --- selection ---------------------------------------------------------------


def test_the_deterministic_structurer_is_the_default(monkeypatch):
    """A clean checkout, CI and the test suite never reach for a key."""
    monkeypatch.setattr(settings, "RESUME_AI_ENABLED", False)
    structurer = get_structurer()
    assert isinstance(structurer, DeterministicResumeStructurer)
    assert structurer.name == "deterministic"


def test_enabling_ai_selects_the_provider_backed_structurer(monkeypatch):
    monkeypatch.setattr(settings, "RESUME_AI_ENABLED", True)
    monkeypatch.setattr(settings, "RESUME_AI_PROVIDER", "anthropic")
    assert isinstance(get_structurer(), ProviderResumeStructurer)


def test_the_deterministic_structurer_still_works_with_ai_switched_off(document):
    """Kept as the fallback and the control, not as dead code."""
    result = DeterministicResumeStructurer().structure(document)
    assert result.structurer == "deterministic"
    assert result.provider is None
    assert result.schema_version == CANDIDATE_SCHEMA_VERSION


def test_an_unconfigured_provider_says_so_rather_than_returning_nothing():
    with pytest.raises(ProviderError) as raised:
        UnconfiguredProvider().complete(
            system="s", user="u", output_model=ResumeCandidate
        )
    assert raised.value.code == "ai_not_configured"


def test_an_unknown_provider_name_is_refused(monkeypatch):
    with pytest.raises(ProviderError):
        get_provider("a-vendor-we-have-no-adapter-for")


# --- the structurer on top of it ---------------------------------------------


def test_the_structurer_carries_attribution_for_the_call_that_happened(document):
    """Auditability: provider, model, prompt and schema travel with the reading."""

    def handler(request):
        return message(json.dumps(CANDIDATE))

    result = ProviderResumeStructurer(build_provider(handler)).structure(document)

    assert result.structurer == "provider"
    assert result.provider == "anthropic"
    assert result.model == settings.RESUME_AI_MODEL
    assert result.prompt_version
    assert result.schema_version == CANDIDATE_SCHEMA_VERSION
    assert result.metadata["input_tokens"] == 120


def test_the_structurer_sends_page_markers_the_model_can_cite(document):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    ProviderResumeStructurer(build_provider(handler)).structure(document)

    prompt = captured["body"]["messages"][0]["content"]
    assert '<page number="1">' in prompt
