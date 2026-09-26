"""The offline repair breakdown (`evaluation/tailoring_repairs.py`) and the runner's capture.

A document the deterministic generator wrote for a frozen `tailoring_v1` case is
damaged in known ways — exactly the kinds of damage a model does — and the
breakdown must attribute each repair to the grounding rule that caused it.
Every network and provider entry point is booby-trapped for the whole module,
so a diagnostic that reached for one would fail here rather than cost a call.
"""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from app.services.tailoring import generation, grounding, reconciliation
from app.services.tailoring.generation import (
    DeterministicTailor,
    GenerationResult,
    TailoringInputs,
    requirement_labels,
)
from evaluation import tailoring_repairs, tailoring_runner
from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job


class _NoNetwork(Exception):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise _NoNetwork("the diagnostic path tried to reach a provider or the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(generation.ProviderTailor, "__init__", refuse)
    monkeypatch.setattr(generation.ProviderTailor, "generate", refuse)
    monkeypatch.setattr(generation, "get_provider", refuse)
    import app.services.tailoring.providers.base as provider_base

    monkeypatch.setattr(provider_base, "get_provider", refuse)


@pytest.fixture(scope="module")
def frozen():
    dataset = cases()
    case = dataset["cases"][0]
    version = confirmed_version()
    job, requirements = structured_job(case["job"])
    clean = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document
    labels = frozenset(requirement_labels(requirements))
    return case, version, clean, labels


def damaged(clean):
    """Four kinds of damage, each with a known repair."""
    first = clean.experience[0]
    bullet = first.bullets[0]
    bullets = list(first.bullets)
    # 1. an invented number AND an invented name on one line: two errors, one removal
    bullets[0] = bullet.model_copy(update={"text": bullet.text + " Saved 97% using Zorblax."})
    # 2. a quote the profile does not contain: one error, one removal
    if len(bullets) > 1:
        bullets[1] = bullets[1].model_copy(update={"source_quote": "Words the profile never said."})
    experience = [
        # 3. an altered copied field: one error, one restoration
        first.model_copy(update={"company": first.company + " Ltd", "bullets": bullets}),
        *clean.experience[1:],
    ]
    skills = list(clean.skills)
    # 4. an unlisted skill: one error, one removal
    skills[0] = skills[0].model_copy(update={"skills": [*skills[0].skills, "Fortran77"]})
    return clean.model_copy(update={"experience": experience, "skills": skills})


def test_a_clean_document_needs_no_repairs(frozen):
    case, version, clean, labels = frozen
    result = tailoring_repairs.replay(case["id"], 0, clean, version, labels)
    assert result.repairs == 0 and result.rows == () and result.delivered


def test_each_repair_is_attributed_to_the_rule_that_caused_it(frozen):
    case, version, clean, labels = frozen
    document = damaged(clean)
    result = tailoring_repairs.replay(case["id"], 3, document, version, labels)

    settled = reconciliation.settle(document, version, job_requirement_labels=labels)
    assert result.repairs == len(settled.repairs)
    assert result.errors_before == settled.errors_before
    assert result.delivered is True

    by_path = {row.field_path: row for row in result.rows}
    assert by_path["experience[0].company"].action == "restored"
    assert by_path["experience[0].company"].rule == "field_altered"
    assert by_path["experience[0].company"].source_field == "company"

    number_line = by_path["experience[0].bullets[0]"]
    assert number_line.action == "removed"
    assert number_line.rule == "invented_name+invented_number"
    assert number_line.source_field == clean.experience[0].bullets[0].source_field

    if len(clean.experience[0].bullets) > 1:
        assert by_path["experience[0].bullets[1]"].rule == "quote_not_found"

    skill = next(row for row in result.rows if row.section == "skills")
    assert (skill.action, skill.rule, skill.claim, skill.source_field) == (
        "removed", "unlisted_skill", "Fortran77", "skills",
    )
    assert all(row.total_repairs == result.repairs for row in result.rows)
    assert all(row.document == f"{case['id']}#3" for row in result.rows)
    assert "unmatched" not in {row.rule for row in result.rows}


def test_claims_can_be_left_out(frozen):
    case, version, clean, labels = frozen
    result = tailoring_repairs.replay(
        case["id"], 0, damaged(clean), version, labels, include_claims=False
    )
    assert all(row.claim is None for row in result.rows)


def test_every_grounding_reason_has_a_rule_name():
    reasons = {v for k, v in vars(grounding).items() if k.startswith("REASON_")}
    assert reasons == set(tailoring_repairs.RULES)


def test_every_repair_reason_has_an_action():
    reasons = {
        v for k, v in vars(reconciliation).items() if k.startswith("REASON_") and isinstance(v, str)
    }
    assert reasons == set(tailoring_repairs.ACTIONS)


class _Scripted:
    """Stands in for a model: returns the damaged document, labelled as AI."""

    name = generation.GENERATOR_AI

    def __init__(self, document, fail_on: str | None = None):
        self._document = document
        self._fail_on = fail_on

    def generate(self, inputs):
        if self._fail_on and inputs.job.job_title == self._fail_on:
            error = RuntimeError("down")
            error.code = "ai_provider_unavailable"
            raise error
        return GenerationResult(
            document=self._document,
            generator=generation.GENERATOR_AI,
            model="scripted",
            prompt_version="test",
            metadata={},
        )


def test_the_runner_capture_replays_to_the_same_counts_offline(frozen, tmp_path, capsys):
    case, version, clean, labels = frozen
    capture = tmp_path / "capture.jsonl"
    document = damaged(clean)

    scores = tailoring_runner.run(tailor=_Scripted(document), samples=2, capture=capture)

    records = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(scores)
    assert all("document" in r and r["fingerprints"] == tailoring_repairs.fingerprints() for r in records)

    replays, failures = tailoring_repairs.replay_capture(records)
    assert not failures
    ours = {(r.case_id, r.sample): r.repairs for r in replays}
    assert sum(ours.values()) == sum(s.repairs for s in scores if s.delivered)
    for score in scores:
        assert score.repairs in ours.values()

    assert tailoring_repairs.main([str(capture)]) == 0
    printed = capsys.readouterr().out
    for heading in (
        "repairs / delivered document, by case",
        "repairs by grounding rule",
        "repairs by source field",
        "removals vs restorations",
        "by case and grounding rule",
    ):
        assert heading in printed


def test_capture_records_failures_and_scoring_is_unchanged(frozen, tmp_path):
    case, version, clean, labels = frozen
    job, _ = structured_job(case["job"])
    tailor = _Scripted(damaged(clean), fail_on=job.job_title)

    with_capture = tailoring_runner.run(tailor=tailor, samples=1, capture=tmp_path / "c.jsonl")
    without = tailoring_runner.run(tailor=tailor, samples=1)

    assert with_capture == without
    records = [json.loads(line) for line in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["error"] for r in records if "error" in r] == ["ai_provider_unavailable"]
    assert sum(1 for s in with_capture if s.error) == 1
    _, failures = tailoring_repairs.replay_capture(records)
    assert failures == {"ai_provider_unavailable": 1}


def test_the_runner_refuses_an_existing_capture_before_any_provider_call(tmp_path):
    """Reusing a path would mix two runs. Refused up front; the offline fixture
    booby-traps ProviderTailor, so reaching it would fail this test differently."""
    existing = tmp_path / "run.jsonl"
    existing.write_text('{"case_id": "x", "sample": 0, "error": "ai_timeout"}\n', encoding="utf-8")

    with pytest.raises(SystemExit) as refused:
        tailoring_runner.main(["--tailor", "ai", "--samples", "5", "--capture", str(existing)])

    assert "already exists" in str(refused.value)
    assert "--overwrite-capture" in str(refused.value)
    assert existing.read_text(encoding="utf-8").count("\n") == 1


def _captured(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_runner_writes_a_new_capture_path(tmp_path, capsys):
    capture = tmp_path / "nested" / "run.jsonl"

    assert tailoring_runner.main(["--tailor", "deterministic", "--capture", str(capture)]) == 0

    records = _captured(capture)
    assert [(r["case_id"], r["sample"]) for r in records] == [
        (case["id"], 0) for case in cases()["cases"]
    ]
    assert all(r["generator"] == "deterministic" and "document" in r for r in records)
    assert all(r["fingerprints"] == tailoring_repairs.fingerprints() for r in records)


def test_overwrite_replaces_the_file_with_exactly_one_run(tmp_path, capsys):
    capture = tmp_path / "run.jsonl"
    capture.write_text('{"case_id": "stale", "sample": 9, "error": "ai_timeout"}\n', encoding="utf-8")
    argv = ["--tailor", "deterministic", "--samples", "2", "--capture", str(capture), "--overwrite-capture"]

    assert tailoring_runner.main(argv) == 0
    first = capture.read_text(encoding="utf-8")
    assert tailoring_runner.main(argv) == 0
    second = capture.read_text(encoding="utf-8")

    records = _captured(capture)
    assert all(r["case_id"] != "stale" for r in records)
    assert len(records) == 2 * len(cases()["cases"])
    assert sorted({r["sample"] for r in records}) == [0, 1]
    # Deterministic: the same run over the same file leaves byte-identical content.
    assert first == second


def test_overwrite_without_a_capture_path_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as refused:
        tailoring_runner.main(["--tailor", "deterministic", "--overwrite-capture"])

    assert refused.value.code == 2
    assert "--overwrite-capture requires --capture" in capsys.readouterr().err


def test_a_capture_from_different_grounding_code_is_flagged(tmp_path):
    record = {"case_id": "x", "sample": 0, "error": "ai_timeout",
              "fingerprints": {"grounding": "0" * 16, "reconciliation": "0" * 16}}
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    _, warnings = tailoring_repairs.read_capture(path)
    assert warnings and "changed since capture" in warnings[0]
