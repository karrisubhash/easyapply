"""The benchmark machinery: loading, validating, measuring, reporting.

What is asserted here is the apparatus, never a score — the same line
`test_matching_evaluation.py` and `test_resume_evaluation.py` draw, and for the
same reason. The engine's agreement with the human judgments is a measurement
of the engine. Pinning it would turn a number nobody has yet interpreted into
a gate, and the first rule change that improved nine cases and cost one would
go red for the wrong reason.

The one number this file does pin is structural: that every requirement a
posting states has a human verdict. A benchmark quietly measuring two-thirds of
its dataset is worse than one that will not run.

Independence — the property the benchmark rests on — is proved separately in
`test_benchmark_independence.py`.
"""

import json
from dataclasses import replace
from datetime import date

import pytest

from evaluation.benchmark import failures as failure_analysis
from evaluation.benchmark import runner
from evaluation.benchmark.adapter import DeterministicEngineAdapter, build
from evaluation.benchmark.groundtruth import DEFAULT_VERSION, available, load
from evaluation.benchmark.metrics import Observation, Rate, Report, confusion
from evaluation.benchmark.schema import (
    VERDICT_MATCHED,
    VERDICT_MISSING,
    VERDICT_UNCERTAIN,
    Dataset,
    DatasetError,
    ExpectedEvidence,
    GroundTruth,
    validate,
)


@pytest.fixture(scope="module")
def dataset():
    return load()


@pytest.fixture(scope="module")
def result():
    return runner.run()


# =============================================================================
# The dataset loads, and is complete
# =============================================================================


def test_the_benchmark_dataset_is_discoverable():
    assert DEFAULT_VERSION in available()


def test_the_dataset_loads(dataset):
    assert dataset.version == DEFAULT_VERSION
    assert dataset.profiles
    assert dataset.jobs
    assert dataset.cases


def test_every_requirement_a_posting_states_has_a_human_verdict(dataset):
    """§10's requirement, and the one structural number worth pinning. A
    dataset that judged only the easy requirements would report a flattering
    score without any individual verdict being wrong."""
    for case in dataset.cases:
        job = dataset.jobs[case.job_id]
        judged = {truth.requirement_id for truth in case.ground_truth}
        stated = {requirement.id for requirement in job.requirements}
        assert judged == stated, f"{case.id} judges {judged ^ stated} inconsistently"


def test_every_verdict_carries_a_reviewable_rationale(dataset):
    for case in dataset.cases:
        for truth in case.ground_truth:
            assert len(truth.rationale) >= 15, f"{case.id}/{truth.requirement_id}"


def test_every_cited_ref_exists_in_the_profile_it_cites(dataset):
    for case in dataset.cases:
        known = dataset.profiles[case.profile_id].refs()
        for truth in case.ground_truth:
            for ref in truth.evidence.refs:
                assert ref in known, f"{case.id}/{truth.requirement_id} cites {ref}"


def test_every_case_pins_the_day_it_was_judged_on(dataset):
    """A duration requirement is a question about a moment; a case without an
    as_of date is unreproducible by construction."""
    for case in dataset.cases:
        assert isinstance(case.as_of, date)


def test_the_dataset_covers_the_dimensions_the_milestone_names(dataset):
    """Coverage of the hard shapes, not of volume."""
    aspects = {
        truth.aspect or dataset.jobs[case.job_id].requirement(truth.requirement_id).aspect
        for case in dataset.cases
        for truth in case.ground_truth
    }
    required = {
        "exact_skill",
        "alias",
        "near_synonym",
        "technology",
        "certification",
        "certification_variant",
        "education_level",
        "education_field",
        "related_field",
        "experience_duration",
        "experience_insufficient",
        "experience_ambiguous",
        "project_not_employment",
        "overlapping_employment",
        "seniority",
        "domain",
        "location",
        "free_text",
        "weak_evidence",
        "missing_evidence",
        "prose_evidence",
        "uncorroborated_claim",
    }
    assert required <= aspects, f"not covered: {sorted(required - aspects)}"


def test_the_dataset_expects_all_three_verdicts(dataset):
    """A dataset that only ever expected MATCHED would be satisfied by a
    matcher that said yes to everything."""
    verdicts = {truth.verdict for case in dataset.cases for truth in case.ground_truth}
    assert verdicts == {VERDICT_MATCHED, VERDICT_MISSING, VERDICT_UNCERTAIN}


def test_contested_judgments_record_the_disagreement(dataset):
    contested = [
        truth
        for case in dataset.cases
        for truth in case.ground_truth
        if truth.contested
    ]
    assert contested, "a dataset with no hard calls is unlikely to be honest"
    for truth in contested:
        assert truth.contested_note


def test_the_review_metadata_is_present(dataset):
    assert dataset.review.dataset_version == DEFAULT_VERSION
    assert dataset.review.created
    assert dataset.review.reviewers
    assert dataset.review.methodology


# =============================================================================
# Invalid datasets are rejected
# =============================================================================


def a_dataset(dataset, **truth_overrides) -> Dataset:
    """The real dataset with one judgment replaced, for the rejection tests."""
    case = dataset.cases[0]
    broken = replace(case.ground_truth[0], **truth_overrides)
    return replace(
        dataset,
        cases=(replace(case, ground_truth=(broken, *case.ground_truth[1:])), *dataset.cases[1:]),
    )


def test_an_unknown_verdict_is_rejected(dataset):
    with pytest.raises(DatasetError, match="unknown verdict"):
        validate(a_dataset(dataset, verdict="probably"))


def test_a_matched_verdict_citing_nothing_is_rejected(dataset):
    """A MATCHED that names no profile item is an expectation nobody can
    check — the exact failure the benchmark exists to detect in a matcher."""
    with pytest.raises(DatasetError, match="must name the profile item"):
        validate(
            a_dataset(
                dataset,
                verdict=VERDICT_MATCHED,
                evidence=ExpectedEvidence(source_type="none", refs=()),
            )
        )


def test_a_missing_verdict_claiming_no_evidence_yet_citing_some_is_rejected(dataset):
    with pytest.raises(DatasetError, match="cannot cite any"):
        validate(
            a_dataset(
                dataset,
                verdict=VERDICT_MISSING,
                evidence=ExpectedEvidence(source_type="none", refs=("s_python",)),
            )
        )


def test_a_missing_verdict_naming_a_source_without_an_item_is_rejected(dataset):
    with pytest.raises(DatasetError, match="must name which item"):
        validate(
            a_dataset(
                dataset,
                verdict=VERDICT_MISSING,
                evidence=ExpectedEvidence(source_type="experience", refs=()),
            )
        )


def test_an_uncertain_verdict_naming_a_source_without_an_item_is_rejected(dataset):
    with pytest.raises(DatasetError, match="must name which item"):
        validate(
            a_dataset(
                dataset,
                verdict=VERDICT_UNCERTAIN,
                evidence=ExpectedEvidence(source_type="skill", refs=()),
            )
        )


def test_citing_an_item_that_is_not_in_the_profile_is_rejected(dataset):
    with pytest.raises(DatasetError, match="not in profile"):
        validate(
            a_dataset(
                dataset,
                evidence=ExpectedEvidence(source_type="skill", refs=("s_does_not_exist",)),
            )
        )


def test_an_unknown_evidence_source_is_rejected(dataset):
    with pytest.raises(DatasetError, match="unknown evidence source"):
        validate(a_dataset(dataset, evidence=ExpectedEvidence(source_type="vibes", refs=())))


def test_an_unreviewable_rationale_is_rejected(dataset):
    with pytest.raises(DatasetError, match="rationale"):
        validate(a_dataset(dataset, rationale="no"))


def test_a_contested_judgment_without_a_note_is_rejected(dataset):
    with pytest.raises(DatasetError, match="without recording"):
        validate(a_dataset(dataset, contested=True, contested_note=None))


def test_an_unknown_aspect_override_is_rejected(dataset):
    with pytest.raises(DatasetError, match="unknown aspect override"):
        validate(a_dataset(dataset, aspect="vibes_based"))


def test_a_requirement_left_unjudged_is_rejected(dataset):
    case = dataset.cases[0]
    short = replace(case, ground_truth=case.ground_truth[1:])
    with pytest.raises(DatasetError, match="no ground-truth verdict"):
        validate(replace(dataset, cases=(short, *dataset.cases[1:])))


def test_a_verdict_for_a_requirement_the_posting_does_not_state_is_rejected(dataset):
    case = dataset.cases[0]
    invented = GroundTruth(
        requirement_id="r_invented",
        verdict=VERDICT_MISSING,
        evidence=ExpectedEvidence(source_type="none"),
        rationale="this requirement does not exist on the posting",
    )
    broken = replace(case, ground_truth=(*case.ground_truth, invented))
    with pytest.raises(DatasetError, match="does not state"):
        validate(replace(dataset, cases=(broken, *dataset.cases[1:])))


def test_a_ground_truth_file_naming_another_dataset_is_rejected(tmp_path, monkeypatch):
    """A ground-truth file copied from elsewhere holds verdicts reached about
    other data."""
    from evaluation.benchmark import groundtruth

    source = groundtruth.DATASETS / DEFAULT_VERSION
    target = tmp_path / "matching_real_v9"
    target.mkdir()
    for name in ("profiles.json", "jobs.json"):
        (target / name).write_text(
            (source / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    payload = json.loads((source / "ground_truth.json").read_text(encoding="utf-8"))
    (target / "ground_truth.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(groundtruth, "DATASETS", tmp_path)
    with pytest.raises(DatasetError, match="declares"):
        groundtruth.load("matching_real_v9")


# =============================================================================
# The metrics are arithmetically right
# =============================================================================


def an_observation(expected, actual, **kwargs):
    base = dict(
        case_id="c",
        requirement_id="r",
        category="skill",
        aspect="exact_skill",
        is_required=True,
        expected=expected,
        actual=actual,
    )
    base.update(kwargs)
    return Observation(**base)


def test_a_rate_with_nothing_to_measure_is_not_zero():
    """None, not 0.0: an unasked question has no answer, and printing 0.00
    invites reading it as a failure."""
    assert Rate(0, 0).value is None
    assert str(Rate(0, 0)).strip() == "n/a"
    assert Rate(3, 4).value == 0.75


def test_precision_and_recall_are_computed_one_vs_rest():
    observations = [
        an_observation(VERDICT_MATCHED, VERDICT_MATCHED),   # tp
        an_observation(VERDICT_MATCHED, VERDICT_MATCHED),   # tp
        an_observation(VERDICT_MISSING, VERDICT_MATCHED),   # fp
        an_observation(VERDICT_MATCHED, VERDICT_MISSING),   # fn
        an_observation(VERDICT_MISSING, VERDICT_MISSING),
    ]
    report = Report(algorithm_version="x", dataset_version="y", observations=observations)

    assert report.matched.true_positive == 2
    assert report.matched.false_positive == 1
    assert report.matched.false_negative == 1
    assert report.matched.precision.value == pytest.approx(2 / 3)
    assert report.matched.recall.value == pytest.approx(2 / 3)
    assert report.matched.f1 == pytest.approx(2 / 3)


def test_f1_is_the_harmonic_mean():
    observations = [an_observation(VERDICT_MATCHED, VERDICT_MATCHED)] * 3
    observations += [an_observation(VERDICT_MISSING, VERDICT_MATCHED)] * 3
    observations += [an_observation(VERDICT_MATCHED, VERDICT_MISSING)]
    report = Report(algorithm_version="x", dataset_version="y", observations=observations)

    precision, recall = report.matched.precision.value, report.matched.recall.value
    assert report.matched.f1 == pytest.approx(2 * precision * recall / (precision + recall))


def test_a_false_match_is_counted_only_in_the_dangerous_direction():
    assert an_observation(VERDICT_MISSING, VERDICT_MATCHED).is_false_match
    assert an_observation(VERDICT_UNCERTAIN, VERDICT_MATCHED).is_false_match
    assert not an_observation(VERDICT_MATCHED, VERDICT_MISSING).is_false_match
    assert not an_observation(VERDICT_MATCHED, VERDICT_MATCHED).is_false_match


def test_the_false_match_rate_excludes_requirements_that_are_genuinely_met():
    """A matcher cannot be blamed for false matches on requirements the
    profile actually satisfies."""
    report = Report(
        algorithm_version="x",
        dataset_version="y",
        observations=[
            an_observation(VERDICT_MATCHED, VERDICT_MATCHED),
            an_observation(VERDICT_MATCHED, VERDICT_MATCHED),
            an_observation(VERDICT_MISSING, VERDICT_MATCHED),
            an_observation(VERDICT_MISSING, VERDICT_MISSING),
        ],
    )
    assert report.false_match_rate.denominator == 2
    assert report.false_match_rate.value == 0.5


def test_the_two_directions_of_uncertainty_error_are_kept_apart():
    report = Report(
        algorithm_version="x",
        dataset_version="y",
        observations=[
            an_observation(VERDICT_MATCHED, VERDICT_UNCERTAIN),
            an_observation(VERDICT_UNCERTAIN, VERDICT_MATCHED),
        ],
    )
    assert len(report.overcautious) == 1
    assert len(report.overconfident) == 1


def test_required_and_preferred_agreement_are_measured_separately():
    report = Report(
        algorithm_version="x",
        dataset_version="y",
        observations=[
            an_observation(VERDICT_MATCHED, VERDICT_MATCHED, is_required=True),
            an_observation(VERDICT_MATCHED, VERDICT_MISSING, is_required=True),
            an_observation(VERDICT_MATCHED, VERDICT_MATCHED, is_required=False),
        ],
    )
    assert report.required_agreement.value == 0.5
    assert report.preferred_agreement.value == 1.0


def test_evidence_is_only_checked_where_checking_it_means_something():
    """Not on a disagreement (that would double-count it) and not on a
    MISSING (which cites nothing by definition)."""
    disagreement = an_observation(
        VERDICT_MATCHED, VERDICT_MISSING, expected_refs=("a",), actual_refs=()
    )
    agreed_miss = an_observation(VERDICT_MISSING, VERDICT_MISSING)
    agreed_match = an_observation(
        VERDICT_MATCHED, VERDICT_MATCHED, expected_refs=("a",), actual_refs=("b",)
    )

    assert not disagreement.evidence_checkable
    assert not agreed_miss.evidence_checkable
    assert agreed_match.evidence_checkable
    assert not agreed_match.evidence_correct


def test_citing_any_of_the_acceptable_items_is_correct():
    """Citing one good reason is not worse than citing every good reason."""
    observation = an_observation(
        VERDICT_MATCHED, VERDICT_MATCHED, expected_refs=("a", "b"), actual_refs=("b",)
    )
    assert observation.evidence_correct


def test_a_misfiled_requirement_is_caught_separately_from_a_wrong_verdict():
    observation = an_observation(
        VERDICT_MATCHED, VERDICT_MATCHED, is_required=True, actual_is_required=False
    )
    assert not observation.classification_correct
    assert failure_analysis.categorise(observation) == failure_analysis.REQUIREMENT_CLASSIFICATION


def test_the_confusion_table_accounts_for_every_observation():
    observations = [
        an_observation(VERDICT_MATCHED, VERDICT_MATCHED),
        an_observation(VERDICT_MATCHED, VERDICT_UNCERTAIN),
        an_observation(VERDICT_MISSING, VERDICT_MISSING),
    ]
    table = confusion(observations)
    assert sum(sum(row.values()) for row in table.values()) == len(observations)
    assert table[VERDICT_MATCHED][VERDICT_UNCERTAIN] == 1


def test_agreement_is_reported_but_is_not_the_only_measure(result):
    """§4: no single misleading headline. The report has to carry the
    asymmetric error counts alongside it."""
    assert result.report.agreement.value is not None
    assert result.report.matched.precision.value is not None
    assert result.report.false_match_rate.value is not None


# =============================================================================
# Failure categorisation
# =============================================================================


def test_the_two_alias_directions_are_opposite_defects():
    missing_alias = an_observation(VERDICT_MATCHED, VERDICT_MISSING, aspect="alias")
    false_alias = an_observation(VERDICT_MISSING, VERDICT_MATCHED, aspect="near_synonym")

    assert failure_analysis.categorise(missing_alias) == failure_analysis.MISSING_ALIAS
    assert failure_analysis.categorise(false_alias) == failure_analysis.FALSE_ALIAS


def test_a_false_alias_is_the_most_severe_thing_reported(result):
    """Ordering is part of the report's meaning: the errors a reader cannot
    see come first."""
    from evaluation.benchmark.failures import Failure

    def a_failure(category, expected, actual):
        return Failure(
            category=category, case_id="c", profile_id="p", profile_label="",
            job_id="j", job_label="", requirement_id="r", requirement_label="",
            requirement_category="skill", aspect="alias", is_required=True,
            expected=expected, actual=actual, expected_rationale="", expected_refs=(),
            actual_explanation=None, actual_refs=(), actual_rule=None, contested=False,
        )

    false_alias = a_failure(failure_analysis.FALSE_ALIAS, VERDICT_MISSING, VERDICT_MATCHED)
    missed = a_failure(failure_analysis.MISSING_ALIAS, VERDICT_MATCHED, VERDICT_MISSING)
    assert false_alias.severity < missed.severity


def test_every_category_a_run_produces_has_a_written_note(result):
    """A failure name nobody can act on is a label, not a diagnosis."""
    for category in failure_analysis.summarise(result.failures):
        assert failure_analysis.CATEGORY_NOTES[category]


def test_an_agreed_verdict_with_correct_evidence_is_not_a_failure():
    observation = an_observation(
        VERDICT_MATCHED, VERDICT_MATCHED, expected_refs=("a",), actual_refs=("a",)
    )
    assert failure_analysis.categorise(observation) == failure_analysis.OTHER


# =============================================================================
# Running: reproducibility, versioning, detail
# =============================================================================


def test_the_run_records_both_versions(result):
    """§8: a result that does not say which dataset and which algorithm
    produced it is uninterpretable the moment either changes."""
    # Never a literal: the version is whatever the engine in this tree says it
    # is, and a test that hardcoded one would have to be edited every time the
    # rules legitimately change — which is the moment it stops being checked.
    assert result.report.algorithm_version == build().version
    assert result.report.algorithm_version
    assert result.report.dataset_version == DEFAULT_VERSION


def test_every_case_is_reproducible(result):
    """Measured inside the runner by evaluating each case twice."""
    assert result.report.irreproducible == []
    assert len(result.report.reproducible) == len(result.dataset.cases)


def test_two_separate_runs_produce_identical_observations():
    """Reproducibility across processes-worth of state, not just within one
    case: the whole benchmark must be re-runnable to the same numbers."""
    first, second = runner.run(), runner.run()

    assert first.report.observations == second.report.observations
    assert first.report.agreement.value == second.report.agreement.value
    assert failure_analysis.summarise(first.failures) == failure_analysis.summarise(
        second.failures
    )


def test_the_algorithm_is_pinned_to_the_profile_and_requirements_it_judged(dataset):
    """§8's other half: a case names the exact profile version and the exact
    requirement set, so a result can be recomputed from the same inputs."""
    adapter = DeterministicEngineAdapter()
    case = dataset.cases[0]
    outcome = adapter.run(
        profile=dataset.profiles[case.profile_id],
        job=dataset.jobs[case.job_id],
        as_of=case.as_of,
    )
    assert outcome.algorithm_version == adapter.version
    assert {v.requirement_id for v in outcome.verdicts} == {
        r.id for r in dataset.jobs[case.job_id].requirements
    }


def test_the_as_of_date_changes_the_answer(dataset):
    """Proof the date is a real input rather than decoration.

    Asserted across the dataset rather than on one case, because it is not
    true of every case and should not be: a verdict reached from a stated
    skill, or from a role with both dates present, is the same on any day. It
    is the ongoing roles measured against "now" that move, and the claim worth
    holding is that at least one case depends on the date — otherwise the
    stored `as_of` would be decoration and the benchmark would be
    reproducible only by accident.
    """
    adapter = DeterministicEngineAdapter()
    moved = []
    for case in dataset.cases:
        profile, job = dataset.profiles[case.profile_id], dataset.jobs[case.job_id]
        now = adapter.run(profile=profile, job=job, as_of=case.as_of)
        long_ago = adapter.run(profile=profile, job=job, as_of=date(2020, 1, 1))
        if now.verdicts != long_ago.verdicts:
            moved.append(case.id)

    assert moved, "no case's verdicts depend on the date they were judged on"


def test_no_requirement_goes_unjudged_by_the_matcher(result):
    assert result.report.unjudged == []


def test_the_matchers_own_summary_agrees_with_its_own_verdicts(result):
    assert result.report.score_inconsistent == []


def test_a_detailed_mismatch_report_is_produced(result):
    """§5: every disagreement must carry the job, the profile, the
    requirement, both verdicts, the human's reason and the matcher's."""
    assert result.failures, "the benchmark found no disagreements at all"
    for failure in result.mismatches:
        assert failure.case_id and failure.profile_id and failure.job_id
        assert failure.requirement_id and failure.requirement_label
        assert failure.expected
        assert failure.expected_rationale
        assert failure.category in failure_analysis.CATEGORIES
        if failure.actual is not None:
            assert failure.actual_explanation
            assert failure.actual_rule


def test_failures_are_grouped_by_category(result):
    grouped = failure_analysis.group(result.failures)
    assert sum(len(items) for items in grouped.values()) == len(result.failures)
    for category in grouped:
        assert category in failure_analysis.CATEGORIES


def test_the_text_report_leads_with_safety_and_ends_with_agreement(result):
    """The ordering is the argument. A report that led with one number would
    be read as one number."""
    text = runner.report_text(result)
    assert text.index("false matches") < text.index("RAW AGREEMENT")
    assert build().version in text
    assert DEFAULT_VERSION in text


def test_the_report_never_speaks_of_hiring_probability(result):
    """§11: this measures coverage against human judgment. Nothing here may
    be phrased as a prediction about a person's prospects."""
    text = runner.report_text(result, show_failures=True).lower()
    for phrase in (
        "probability",
        "likelihood of being hired",
        "chance of an interview",
        "should apply",
        "unsuitable",
        "rejected",
    ):
        assert phrase not in text or "say nothing about" in text


def test_the_json_report_carries_every_measure(result):
    payload = json.loads(runner.report_json(result))

    assert payload["dataset_version"] == DEFAULT_VERSION
    assert payload["algorithm_version"] == build().version
    assert set(payload["safety"]) == {
        "false_matches",
        "false_match_rate",
        "false_missing",
        "unsupported_matches",
    }
    assert set(payload["per_verdict"]) == {"matched", "missing", "uncertain"}
    assert payload["integrity"]["irreproducible_cases"] == 0
    assert payload["evidence"]["correctness"] is not None
    assert payload["by_aspect"]
    assert payload["confusion"]


def test_the_detailed_report_renders(result):
    text = runner.report_text(result, show_failures=True)
    assert "DETAILED DISAGREEMENTS" in text
    for failure in result.failures[:5]:
        assert failure.requirement_label in text
