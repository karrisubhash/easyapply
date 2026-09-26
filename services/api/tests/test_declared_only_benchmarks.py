"""The declared-only datasets, as CI runs them.

Three jobs, and they are deliberately different in kind:

1. **`declared_only_v1` is scored and pinned.** It is an independent benchmark
   with expectations, so its headline numbers are a regression tripwire: if the
   matcher changes, these fail, and they are SUPPOSED to. Updating them is a
   conscious act that belongs in the same change as the algorithm bump, never a
   drive-by edit.

2. **`declared_only_v2` is validated and cannot be scored.** It holds fixtures
   and no expectations on purpose — the evidence shapes `docs/matching-v6-
   declared-only-analysis.md` §G says are still missing, waiting on a human
   reviewer. The most important assertion in this file is the one that says its
   `ground_truth.json` does not exist. If a model ever writes one, that is the
   test that catches it.

3. **The blind review packet stays blind.** The shipped `packet.md` is re-run
   through the builder's own leak check, so a regenerated packet that started
   quoting the first rater's rationales fails here rather than in front of the
   second rater.

None of this imports a V6 policy, because there is no V6 policy to import.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from evaluation.benchmark import groundtruth
from evaluation.benchmark.runner import run

DATASETS = groundtruth.DATASETS
V1 = "declared_only_v1"
V2 = "declared_only_v2"
REVIEW = DATASETS / V1 / "review_r2"

#: What matching_v5 scores on declared_only_v1 today. A tripwire, not a target:
#: this benchmark exists to disagree with the matcher, and a change here means
#: the matcher moved.
V1_EXPECTED = {
    # matching_v5_1 was measured to score exactly what matching_v5 did here.
    "algorithm_version": "matching_v5_1",
    "judgments": 94,
    "agreements": 76,
    "false_matches": 10,
}


def _module(name: str):
    """Load a review_r2 script by path; that directory is not a package."""
    spec = importlib.util.spec_from_file_location(name, REVIEW / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(dataset: str, name: str):
    return json.loads((DATASETS / dataset / name).read_text(encoding="utf-8"))


# --- 1. declared_only_v1, scored -------------------------------------------------


def test_declared_only_v1_scores_exactly_what_it_scored_when_the_analysis_was_written():
    report = run(dataset_version=V1).report
    measured = {
        "algorithm_version": report.algorithm_version,
        "judgments": len(report.observations),
        "agreements": sum(1 for observation in report.observations if observation.agrees),
        "false_matches": len(report.false_matches),
    }
    assert measured == V1_EXPECTED, (
        "declared_only_v1 moved. If the matcher changed on purpose, update V1_EXPECTED in "
        "the same commit and say so in the algorithm's release notes; if it did not, "
        "something changed the engine by accident."
    )


def test_declared_only_v1_ground_truth_is_the_file_that_was_frozen_before_the_first_run():
    digest = __import__("hashlib").sha256(
        (DATASETS / V1 / "ground_truth.json").read_bytes()
    ).hexdigest()
    assert digest == "0f96c6d6374ca781ff4372f67329ec9c83af4b3af4175edfa23abcb2786b1f04", (
        "declared_only_v1's expectations have been edited. They were frozen before any "
        "matcher was run against them, and that freeze is what makes the benchmark evidence "
        "rather than a mirror. A corrected verdict bumps the directory to declared_only_v2."
    )


# --- 2. declared_only_v2, fixtures only ------------------------------------------


def test_declared_only_v2_has_no_expectations_and_none_may_be_added_by_a_model():
    assert not (DATASETS / V2 / "ground_truth.json").exists(), (
        "declared_only_v2 has acquired a ground_truth.json. This dataset exists to hold "
        "fixtures whose expectations a HUMAN reviewer has not written yet. If a human wrote "
        "them, delete this assertion in the same commit that records who and when."
    )
    assert V2 not in groundtruth.available()


@pytest.mark.parametrize("filename", ["profiles.json", "jobs.json", "pairings.json"])
def test_no_declared_only_v2_file_contains_anything_shaped_like_an_expectation(filename):
    raw = (DATASETS / V2 / filename).read_text(encoding="utf-8")
    for forbidden in ('"verdict"', '"rationale"', '"ground_truth"', '"expected"', '"contested"'):
        assert forbidden not in raw, f"{filename} contains {forbidden}, which is an expectation"


def test_declared_only_v2_fixtures_load_with_the_benchmarks_own_loaders():
    profiles = {raw["id"]: groundtruth._profile(raw) for raw in _read(V2, "profiles.json")["profiles"]}
    jobs = {raw["id"]: groundtruth._job(raw) for raw in _read(V2, "jobs.json")["jobs"]}
    assert profiles and jobs
    for job in jobs.values():
        seen = [r.id for r in job.requirements]
        assert len(seen) == len(set(seen)), f"{job.id} states a requirement id twice"


def test_every_declared_only_v2_pairing_resolves_to_real_fixtures_and_real_requirements():
    profiles = {raw["id"] for raw in _read(V2, "profiles.json")["profiles"]}
    jobs = {raw["id"]: {r["id"] for r in raw["requirements"]}
            for raw in _read(V2, "jobs.json")["jobs"]}
    pairings = _read(V2, "pairings.json")["pairings"]
    assert pairings

    for entry in pairings:
        assert entry["profile_id"] in profiles, f"{entry['id']}: unknown profile"
        assert entry["job_id"] in jobs, f"{entry['id']}: unknown job"
        assert date.fromisoformat(entry["as_of"])
        assert entry["requirement_ids"], f"{entry['id']}: judges nothing"
        for rid in entry["requirement_ids"]:
            assert rid in jobs[entry["job_id"]], f"{entry['id']}: no requirement {rid}"


def test_declared_only_v2_states_in_its_own_file_that_its_expectations_are_absent():
    declared = _read(V2, "pairings.json")["expectations"]
    assert declared["state"] == "absent"
    assert "human" in declared["statement"].lower()


# --- 3. the blind review packet ---------------------------------------------------


def test_the_shipped_packet_passes_its_own_leak_check():
    build_packet = _module("build_packet")
    packet = (REVIEW / "packet.md").read_text(encoding="utf-8")
    # Raises SystemExit naming every leak it finds.
    build_packet._check_no_leaks(packet, groundtruth.load(V1), build_packet.fixture_pairings())


def test_the_packet_manifest_and_answer_template_describe_the_same_rows():
    packet = (REVIEW / "packet.md").read_text(encoding="utf-8")
    in_packet = [line[5:].strip() for line in packet.splitlines() if line.startswith("#### R")]
    manifest = [row["row_id"] for row in json.loads((REVIEW / "manifest.json").read_text(encoding="utf-8"))["rows"]]
    template = [row["row_id"] for row in json.loads((REVIEW / "answers_template.json").read_text(encoding="utf-8"))["ratings"]]

    assert in_packet == manifest == template
    assert in_packet == [f"R{index:02d}" for index in range(1, len(in_packet) + 1)]


def test_one_review_session_offers_at_least_thirty_observations():
    manifest = json.loads((REVIEW / "manifest.json").read_text(encoding="utf-8"))["rows"]
    assert len(manifest) >= 30, (
        "The review package is the only route to the evidence V6 needs; a session shorter "
        "than 30 observations cannot reach the pre-registered bar under any outcome."
    )
    # The v1 rows carry a first rating and can enter an agreement statistic; the
    # v2 rows cannot, and the report must keep saying so rather than pooling them.
    assert {row["has_first_rating"] for row in manifest} == {True, False}


def test_the_answer_template_is_blank():
    template = json.loads((REVIEW / "answers_template.json").read_text(encoding="utf-8"))
    for row in template["ratings"]:
        assert row["verdict"] == ""
        assert row["profile_evidence_shape"] == ""
        assert row["rationale"] == ""


def test_the_rubric_offers_a_code_for_every_shape_the_packet_contains():
    rubric = (REVIEW / "RUBRIC.md").read_text(encoding="utf-8")
    analyze = _module("analyze")
    for code in analyze.SHAPES + analyze.DEMANDS:
        assert f"`{code}`" in rubric, f"{code} is accepted by analyze.py but undefined in RUBRIC.md"
