"""The OpenRouter adapter, bound to the tailoring domain.

Nothing here reaches the network: the adapter's `httpx.Client` is given a mock
transport, which is the only way to assert what is actually sent and how each
reply is translated. What matters is the same as for the Anthropic adapter —
the request carries the schema as a hard requirement, the reply is validated by
the Pydantic model and nothing else, every failure lands on a distinguishable
code, and neither the key nor the vendor's text leaves the adapter.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.schemas.tailored_resume import TailoredResumeDocument
from app.services.ai.providers.openrouter import OpenRouterProvider as SharedOpenRouterProvider
from app.services.ai.providers.openrouter import strict_json_schema
from app.services.tailoring.failures import is_retryable
from app.services.tailoring.generation import GENERATOR_AI, ProviderTailor
from app.services.tailoring.providers import ProviderError, get_provider
from app.services.tailoring.providers.openrouter_provider import OpenRouterProvider
from tests.test_tailoring_ai import a_document_from, inputs_for, setup  # noqa: F401

MODEL = "vendor/some-model:free"
SECRET = "sk-or-test-secret-never-printed"


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    note: str | None = None


def completion(content, *, finish_reason="stop", **extra):
    body = {
        "id": "gen-123",
        "model": MODEL,
        "provider": "SomeUpstream",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "completion_tokens_details": {"reasoning_tokens": 5},
            "cost": 0,
        },
    }
    body.update(extra)
    return httpx.Response(200, json=body)


def failure(status, *, code=None, metadata=None, message="upstream said: <the user's resume>"):
    error = {"code": code or status, "message": message}
    if metadata is not None:
        error["metadata"] = metadata
    return httpx.Response(status, json={"error": error})


class Recorder:
    """A transport handler that replays scripted responses and records requests."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, index=0) -> dict:
        return json.loads(self.requests[index].content)


def build(handler, *, max_retries=0, model=MODEL, sleeps=None):
    client = httpx.Client(
        base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler)
    )
    return SharedOpenRouterProvider(
        client=client,
        model=model,
        timeout_seconds=5,
        max_retries=max_retries,
        max_tokens=1234,
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
    )


def ask(provider, output_model=Answer):
    return provider.complete(system="SYSTEM", user="USER TEXT", output_model=output_model)


# --- the request -------------------------------------------------------------------


def test_the_schema_is_sent_as_a_strict_requirement_not_a_suggestion():
    handler = Recorder(completion('{"title": "ok", "note": null}'))
    ask(build(handler))

    sent = handler.body()
    assert handler.requests[0].url.path.endswith("/chat/completions")
    assert sent["model"] == MODEL
    assert sent["max_tokens"] == 1234
    assert sent["messages"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER TEXT"},
    ]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "Answer"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["schema"] == strict_json_schema(Answer)
    # Only endpoints that implement response_format may serve this request.
    assert sent["provider"] == {"require_parameters": True}


def test_no_tools_and_no_sampling_parameters_are_sent():
    handler = Recorder(completion('{"title": "ok", "note": null}'))
    ask(build(handler))

    sent = handler.body()
    for key in ("tools", "tool_choice", "temperature", "top_p", "top_k", "stream"):
        assert key not in sent


def test_the_strict_schema_requires_every_property_and_forbids_extras():
    schema = strict_json_schema(TailoredResumeDocument)

    def objects(node):
        if isinstance(node, dict):
            if isinstance(node.get("properties"), dict):
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    found = list(objects(schema))
    assert len(found) > 5
    for node in found:
        assert node["required"] == list(node["properties"])
        assert node["additionalProperties"] is False
    assert '"default"' not in json.dumps(schema)


def test_a_property_named_like_a_keyword_is_left_alone():
    class Odd(BaseModel):
        default: str = "x"
        required: int = 1

    schema = strict_json_schema(Odd)
    assert set(schema["properties"]) == {"default", "required"}
    assert schema["required"] == ["default", "required"]


def test_the_strict_schema_changes_nothing_about_what_validates(db, user, setup):
    """Every field present, nulls explicit — what a strict model sends — still validates."""
    version, _, _ = setup
    document = a_document_from(version)
    as_strict_model_would_send = document.model_dump(mode="json")

    assert TailoredResumeDocument.model_validate(as_strict_model_would_send) == document


# --- the reply ---------------------------------------------------------------------


def test_the_adapter_returns_the_requested_model():
    result = ask(build(Recorder(completion('{"title": "hello", "note": null}'))))

    assert isinstance(result.output, Answer)
    assert result.output == Answer(title="hello")


def test_one_code_fence_around_the_whole_reply_is_tolerated():
    fenced = '```json\n{"title": "hello", "note": null}\n```'
    assert ask(build(Recorder(completion(fenced)))).output.title == "hello"


def test_content_parts_are_joined():
    parts = [{"type": "text", "text": '{"title": '}, {"type": "text", "text": '"hi", "note": null}'}]
    assert ask(build(Recorder(completion(parts)))).output.title == "hi"


def test_metadata_is_counts_and_ids_but_never_content():
    result = ask(build(Recorder(completion('{"title": "SECRET WORDS", "note": null}'))))

    assert result.metadata == {
        "latency_ms": result.metadata["latency_ms"],
        "attempts": 1,
        "model": MODEL,
        "upstream_provider": "SomeUpstream",
        "stop_reason": "stop",
        "response_id": "gen-123",
        "input_tokens": 11,
        "output_tokens": 22,
        "reasoning_tokens": 5,
        "cost": 0,
    }
    assert "SECRET WORDS" not in json.dumps(result.metadata)


def test_output_that_is_not_json_is_malformed_not_schema_invalid():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(completion("Sure! Here is the resume you asked for."))))
    assert caught.value.code == "ai_output_malformed"


def test_valid_json_of_the_wrong_shape_fails_schema_validation():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(completion('{"headline": "not the schema"}'))))
    assert caught.value.code == "ai_output_invalid"


def test_a_reply_cut_off_at_the_token_ceiling_is_malformed():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(completion(None, finish_reason="length"))))
    assert caught.value.code == "ai_output_malformed"


def test_a_refusal_is_reported_rather_than_parsed():
    response = completion('{"title": "x", "note": null}')
    body = json.loads(response.content)
    body["choices"][0]["message"]["refusal"] = "I can't help with that."
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(httpx.Response(200, json=body))))
    assert caught.value.code == "ai_content_rejected"


def test_a_content_filter_stop_is_a_content_rejection():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(completion(None, finish_reason="content_filter"))))
    assert caught.value.code == "ai_content_rejected"


def test_an_upstream_error_inside_a_200_is_classified_not_parsed():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(httpx.Response(200, json={"error": {"code": 402, "message": "no"}}))))
    assert caught.value.code == "ai_billing_blocked"


def test_a_non_json_body_is_malformed():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(httpx.Response(200, text="<html>gateway</html>"))))
    assert caught.value.code == "ai_output_malformed"


# --- failures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (failure(400), "ai_request_invalid"),
        (failure(401), "ai_authentication_failed"),
        (failure(402), "ai_billing_blocked"),
        (failure(403, metadata={"reasons": ["x"], "flagged_input": "..."}), "ai_content_rejected"),
        (failure(403), "ai_unauthorized"),
        (failure(404), "ai_request_invalid"),
        (failure(408), "ai_timeout"),
        (failure(429), "ai_rate_limited"),
        (failure(500), "ai_provider_error"),
        (failure(502), "ai_provider_unavailable"),
        (failure(503), "ai_provider_unavailable"),
        (httpx.Response(500, text="not json"), "ai_provider_error"),
    ],
)
def test_http_failures_map_to_the_domain_vocabulary(response, expected):
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(response)))
    assert caught.value.code == expected


def test_the_operator_facing_codes_are_all_distinguishable():
    codes = {}
    for status in (401, 402, 429, 400, 503):
        with pytest.raises(ProviderError) as caught:
            ask(build(Recorder(failure(status))))
        codes[status] = caught.value.code
    with pytest.raises(ProviderError) as missing:
        ask(build(Recorder(), model=None))
    codes["unset"] = missing.value.code

    assert codes == {
        401: "ai_authentication_failed",
        402: "ai_billing_blocked",
        429: "ai_rate_limited",
        400: "ai_request_invalid",
        503: "ai_provider_unavailable",
        "unset": "ai_not_configured",
    }
    assert len(set(codes.values())) == 6


def test_a_timeout_is_reported_as_a_timeout():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(httpx.ReadTimeout("slow"))))
    assert caught.value.code == "ai_timeout"


def test_an_unreachable_provider_is_distinguished_from_a_rejection():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(httpx.ConnectError("dns"))))
    assert caught.value.code == "ai_provider_unavailable"


def test_a_transient_failure_is_retried_within_the_cap():
    sleeps: list[float] = []
    handler = Recorder(
        httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"code": 429}}),
        completion('{"title": "ok", "note": null}'),
    )
    result = ask(build(handler, max_retries=1, sleeps=sleeps))

    assert result.output.title == "ok"
    assert result.metadata["attempts"] == 2
    assert len(handler.requests) == 2
    assert sleeps == [3.0]


def test_a_long_retry_after_is_capped():
    sleeps: list[float] = []
    handler = Recorder(
        httpx.Response(429, headers={"retry-after": "86400"}, json={"error": {"code": 429}}),
        completion('{"title": "ok", "note": null}'),
    )
    ask(build(handler, max_retries=1, sleeps=sleeps))
    assert sleeps == [30.0]


def test_with_no_retries_exactly_one_request_is_made():
    handler = Recorder(failure(503))
    with pytest.raises(ProviderError):
        ask(build(handler, max_retries=0))
    assert len(handler.requests) == 1


def test_a_final_failure_is_not_retried():
    handler = Recorder(failure(402), failure(402))
    with pytest.raises(ProviderError) as caught:
        ask(build(handler, max_retries=3))
    assert caught.value.code == "ai_billing_blocked"
    assert len(handler.requests) == 1


def test_a_provider_error_never_quotes_the_vendor_or_the_request():
    with pytest.raises(ProviderError) as caught:
        ask(build(Recorder(failure(400, message="echo: USER TEXT and more"))))
    assert "USER TEXT" not in str(caught.value)
    assert "echo" not in caught.value.message


@pytest.mark.parametrize("code", ["ai_authentication_failed", "ai_billing_blocked"])
def test_the_new_codes_are_permanent_failures(code):
    assert is_retryable(code) is False


# --- configuration -----------------------------------------------------------------


def test_a_missing_key_is_a_configuration_failure_and_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    provider = SharedOpenRouterProvider(
        model=MODEL, timeout_seconds=5, max_retries=0, max_tokens=10
    )
    with pytest.raises(ProviderError) as caught:
        ask(provider)
    assert caught.value.code == "ai_not_configured"


def test_the_key_is_sent_as_a_bearer_token_and_nowhere_else(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", SECRET)
    handler = Recorder(completion('{"title": "ok", "note": null}'))
    provider = SharedOpenRouterProvider(
        model=MODEL, timeout_seconds=5, max_retries=0, max_tokens=10
    )
    real_client = provider._get_client()
    real_client._transport = httpx.MockTransport(handler)

    result = ask(provider)

    assert handler.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in handler.requests[0].content.decode()
    assert SECRET not in json.dumps(result.metadata)


def test_openrouter_is_selectable_for_tailoring(monkeypatch):
    monkeypatch.setattr(settings, "TAILORING_AI_MODEL", MODEL)
    monkeypatch.setattr(
        settings, "__pydantic_fields_set__", settings.model_fields_set | {"TAILORING_AI_MODEL"}
    )

    provider = get_provider("openrouter")

    assert isinstance(provider, OpenRouterProvider)
    assert provider.name == "openrouter"
    assert provider.model == MODEL


def test_the_claude_default_is_never_sent_to_openrouter(monkeypatch):
    monkeypatch.setattr(
        settings, "__pydantic_fields_set__", settings.model_fields_set - {"TAILORING_AI_MODEL"}
    )
    provider = OpenRouterProvider(client=httpx.Client(transport=httpx.MockTransport(Recorder())))

    assert provider.model is None
    with pytest.raises(ProviderError) as caught:
        ask(provider)
    assert caught.value.code == "ai_not_configured"


def test_openrouter_is_not_registered_for_the_other_domains():
    from app.services.job.providers import get_provider as job_provider
    from app.services.resume.providers.base import get_provider as resume_provider

    for factory in (job_provider, resume_provider):
        with pytest.raises(ProviderError) as caught:
            factory("openrouter")
        assert caught.value.code == "ai_not_configured"


def test_selecting_openrouter_does_not_switch_ai_on():
    assert settings.TAILORING_AI_ENABLED is False


# --- through the tailor ------------------------------------------------------------


def test_the_tailor_gets_a_validated_document_through_openrouter(db, user, setup):
    version, job, requirements = setup
    document = a_document_from(version)
    handler = Recorder(completion(document.model_dump_json()))

    result = ProviderTailor(provider=build(handler)).generate(inputs_for(version, job, requirements))

    assert result.document == document
    assert result.generator == GENERATOR_AI
    assert result.model == MODEL
    assert result.metadata["input_tokens"] == 11
    sent = handler.body()
    assert sent["response_format"]["json_schema"]["name"] == "TailoredResumeDocument"
