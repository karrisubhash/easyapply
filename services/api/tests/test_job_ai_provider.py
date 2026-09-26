"""The job domain's AI provider boundary.

The mirror of `test_resume_provider.py`. Nothing here reaches the network: the
Anthropic SDK is exercised against a mock transport, which is the only way to
assert what is actually sent and how the reply is translated — the two things
that matter and that a stubbed client would let us skip. Most of the HTTP
translation is shared code (`app/services/ai/providers/anthropic.py`), already
covered by the resume suite; what is specific to this domain is the settings
binding, the job-specific structurer wiring, and the job failure vocabulary.
"""

import json

import httpx2 as httpx
import pytest

from app.core.config import settings
from app.schemas.job_candidate import JOB_CANDIDATE_SCHEMA_VERSION, JobCandidate
from app.services.job.providers import ProviderError, get_provider
from app.services.job.providers.anthropic_provider import AnthropicProvider
from app.services.job.providers.base import UnconfiguredProvider
from app.services.job.structuring import (
    DeterministicJobStructurer,
    ProviderJobStructurer,
    get_job_structurer,
)
from tests.job_fixtures import make_job_payload

CANDIDATE = {
    "required_skills": [],
    "preferred_skills": [],
    "required_experience": [
        {
            "description": "5+ years of Python",
            "minimum_years": 5,
            "maximum_years": None,
            "provenance": [
                {"source_section": "required", "quote": "5+ years of Python", "confidence": 0.9}
            ],
        }
    ],
    "preferred_experience": [],
    "education": [],
    "certifications": [],
    "responsibilities": [],
    "technologies": [
        {
            "name": "Python",
            "category": None,
            "is_required": True,
            "provenance": [
                {"source_section": "required", "quote": "5+ years of Python", "confidence": 0.9}
            ],
        }
    ],
    "qualifications": [],
}


def build_provider(handler):
    """A job AnthropicProvider whose transport is under the test's control."""
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
            "usage": {"input_tokens": 90, "output_tokens": 210},
        },
    )


def refusal():
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
            "usage": {"input_tokens": 90, "output_tokens": 0},
        },
    )


# --- the happy path ----------------------------------------------------------


def test_the_adapter_returns_the_requested_model():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    completion = build_provider(handler).complete(
        system="rules", user="job text", output_model=JobCandidate
    )

    assert isinstance(completion.output, JobCandidate)
    assert completion.output.technologies[0].name == "Python"

    fmt = captured["body"]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "$defs" in fmt["schema"]


def test_the_job_adapter_uses_its_own_settings_not_resumes(monkeypatch):
    """JOB_AI_MODEL and RESUME_AI_MODEL must be free to diverge — that is the
    entire reason each domain has its own subclass over the shared adapter."""
    monkeypatch.setattr(settings, "JOB_AI_MODEL", "claude-job-test-model")
    monkeypatch.setattr(settings, "RESUME_AI_MODEL", "claude-resume-test-model")

    def handler(request):
        return message(json.dumps(CANDIDATE))

    provider = build_provider(handler)
    assert provider.model == "claude-job-test-model"


def test_no_tools_are_offered_to_the_model():
    """No tool execution based on job content: the request never advertises
    any tool the model could ask to call, so there is nothing for injected
    text to try to invoke."""
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    build_provider(handler).complete(system="rules", user="text", output_model=JobCandidate)

    assert "tools" not in captured["body"]
    assert "tool_choice" not in captured["body"]


def test_no_sampling_parameters_are_sent():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return message(json.dumps(CANDIDATE))

    build_provider(handler).complete(system="rules", user="text", output_model=JobCandidate)

    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in captured["body"]


def test_metadata_is_counts_and_ids_but_never_content():
    def handler(request):
        return message(json.dumps(CANDIDATE))

    completion = build_provider(handler).complete(
        system="rules", user="SECRET JOB DESCRIPTION TEXT", output_model=JobCandidate
    )

    assert completion.metadata["input_tokens"] == 90
    assert completion.metadata["output_tokens"] == 210
    assert completion.metadata["stop_reason"] == "end_turn"

    serialised = json.dumps(completion.metadata)
    assert "SECRET JOB DESCRIPTION" not in serialised
    assert "test-key" not in serialised


# --- failure classification --------------------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [
        (429, "ai_rate_limited"),
        (401, "ai_unauthorized"),
        (400, "ai_request_invalid"),
        (500, "ai_provider_error"),
    ],
)
def test_http_failures_map_to_the_domain_vocabulary(status, expected):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == expected


def test_a_timeout_is_reported_as_a_timeout():
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_timeout"


def test_an_unreachable_provider_is_distinguished_from_a_rejection():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_provider_unavailable"


def test_output_that_is_not_json_is_malformed_not_schema_invalid():
    def handler(request):
        return message('{"required_skills": [ truncated mid')

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_output_malformed"


def test_valid_json_of_the_wrong_shape_fails_schema_validation():
    def handler(request):
        return message(json.dumps({"required_skills": "not a list"}))

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_output_invalid"


def test_a_policy_refusal_is_reported_rather_than_parsed():
    def handler(request):
        return refusal()

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_content_rejected"


def test_a_missing_key_is_a_configuration_failure_not_an_outage(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    with pytest.raises(ProviderError) as raised:
        AnthropicProvider().complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_not_configured"


def test_a_provider_error_never_quotes_the_request():
    def handler(request):
        return httpx.Response(
            400, json={"error": {"message": "bad input: Acme Corp, Confidential Role"}}
        )

    with pytest.raises(ProviderError) as raised:
        build_provider(handler).complete(
            system="s", user="Acme Corp, Confidential Role", output_model=JobCandidate
        )
    assert "Acme Corp" not in raised.value.message
    assert "Confidential Role" not in raised.value.message


# --- selection -----------------------------------------------------------


def test_the_deterministic_structurer_is_the_default(monkeypatch):
    """A clean checkout, CI and the test suite never reach for a key."""
    monkeypatch.setattr(settings, "JOB_AI_ENABLED", False)
    structurer = get_job_structurer()
    assert isinstance(structurer, DeterministicJobStructurer)
    assert structurer.name == "deterministic"


def test_enabling_job_ai_selects_the_provider_backed_structurer_independently_of_resume(monkeypatch):
    """JOB_AI_ENABLED and RESUME_AI_ENABLED must be independent toggles."""
    monkeypatch.setattr(settings, "JOB_AI_ENABLED", True)
    monkeypatch.setattr(settings, "JOB_AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "RESUME_AI_ENABLED", False)
    assert isinstance(get_job_structurer(), ProviderJobStructurer)


def test_an_unconfigured_job_provider_says_so_rather_than_returning_nothing():
    with pytest.raises(ProviderError) as raised:
        UnconfiguredProvider().complete(system="s", user="u", output_model=JobCandidate)
    assert raised.value.code == "ai_not_configured"


def test_an_unknown_job_provider_name_is_refused():
    with pytest.raises(ProviderError):
        get_provider("a-vendor-we-have-no-adapter-for")


# --- the structurer on top of it ---------------------------------------------


def test_the_structurer_carries_attribution_for_the_call_that_happened(db, user):
    def handler(request):
        return message(json.dumps(CANDIDATE))

    payload = make_job_payload(external_job_id="attribution-1")
    from app.models.job import Job

    job = Job(
        user_id=user.id,
        source_type=payload["source_type"],
        source_url=payload["source_url"],
        canonical_url=payload["source_url"],
        external_job_id=payload["external_job_id"],
        content_hash="a" * 64,
        job_title=payload["job_title"],
        company_name=payload["company_name"],
        description_text=payload["description_text"],
        raw_payload={},
    )
    db.add(job)
    db.flush()

    result = ProviderJobStructurer(build_provider(handler)).structure(job)

    assert result.structurer == "provider"
    assert result.provider == "anthropic"
    assert result.model == settings.JOB_AI_MODEL
    assert result.prompt_version
    assert result.schema_version == JOB_CANDIDATE_SCHEMA_VERSION
    assert result.metadata["input_tokens"] == 90
