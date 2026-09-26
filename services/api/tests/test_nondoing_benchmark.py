"""The non_doing_v1 benchmark machinery: loading, validating, scoring, reporting.

What is pinned here is the apparatus, never a score - the same line
`test_benchmark.py` draws for the real-data matching benchmark, and for the
same reason: the implementation's agreement with this dataset is a
measurement of the implementation, not a threshold to gate on.

Independence is proved separately in
`test_nondoing_benchmark_independence.py`.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evaluation.nondoing import groundtruth, runner
from evaluation.nondoing.metrics import PipelineObservation, Rate, Report, SentenceObservation
from evaluation.nondoing.schema import (
    DEFECT_CLASSES,
    NON_DOING_MARKER_VOCABULARY,
    PIPELINE_CATEGORIES,
    SENTENCE_CATEGORIES,
    Dataset,
    DatasetError,
    DurationExpectation,
    PipelineExpected,
    SentenceCase,
    TokenExpectation,
    validate,
)


@pytest.fixture(scope="module")
def dataset():
    return groundtruth.load()


@pytest.fixture(scope="module")
def result():
    return runner.run()


# =============================================================================
# The dataset loads, and covers what it claims to
# =============================================================================


def test_the_dataset_loads(dataset):
    assert dataset.version == groundtruth.DEFAULT_VERSION
    assert dataset.sentence_cases
    assert dataset.pipeline_cases


def test_the_dataset_is_within_the_target_size(dataset):
    assert 130 <= dataset.case_count <= 170


def test_every_marker_is_tagged_on_at_least_one_case(dataset):
    covered = {marker for case in dataset.sentence_cases for marker in case.markers}
    covered |= {marker for case in dataset.pipeline_cases for marker in case.markers}
    missing = NON_DOING_MARKER_VOCABULARY - covered
    assert not missing, f"markers with no coverage at all: {sorted(missing)}"


def test_every_required_sentence_category_is_present(dataset):
    present = {case.category for case in dataset.sentence_cases}
    assert set(SENTENCE_CATEGORIES) <= present


def test_every_required_pipeline_category_is_present(dataset):
    present = {case.category for case in dataset.pipeline_cases}
    assert set(PIPELINE_CATEGORIES) <= present


def test_both_constructions_are_represented(dataset):
    constructions = {case.construction for case in dataset.sentence_cases}
    assert constructions == {"positive", "negative"}


def test_minimal_pairs_exist_and_genuinely_contrast(dataset):
    pairs: dict[str, list[bool]] = {}
    for case in dataset.sentence_cases:
        if case.pair_id:
            pairs.setdefault(case.pair_id, []).append(case.expected_non_doing)
    assert len(pairs) >= 5, "too few minimal pairs to call this real coverage"
    for pair_id, values in pairs.items():
        assert len(set(values)) > 1, f"{pair_id} does not contrast"


def test_technology_attribution_is_tested_with_two_technologies_sharing_a_clause(dataset):
    attribution_cases = [
        c for c in dataset.pipeline_cases if c.category == "technology_attribution"
    ]
    assert attribution_cases
    for case in attribution_cases:
        assert len(case.expected.tokens) >= 2, case.id


def test_job_title_path_is_tested_separately_from_description(dataset):
    title_cases = [c for c in dataset.pipeline_cases if c.category == "job_title_path"]
    assert len(title_cases) >= 3


def test_duration_and_evidence_tier_consequences_are_both_tested(dataset):
    categories = {c.category for c in dataset.pipeline_cases}
    assert "duration_consequence" in categories
    assert "evidence_tier_consequence" in categories


def test_every_defect_class_is_used_at_least_once(dataset):
    used = {case.defect_class for case in dataset.sentence_cases}
    used |= {case.defect_class for case in dataset.pipeline_cases}
    assert set(DEFECT_CLASSES) <= used, f"unused: {set(DEFECT_CLASSES) - used}"


def test_the_review_metadata_is_present(dataset):
    assert dataset.review.created
    assert dataset.review.methodology
    assert dataset.review.independence_statement


# =============================================================================
# Invalid datasets are rejected
# =============================================================================


def test_an_unknown_sentence_category_is_rejected(dataset):
    broken = replace(dataset.sentence_cases[0], category="vibes")
    with pytest.raises(DatasetError, match="unknown category"):
        validate(replace(dataset, sentence_cases=(broken, *dataset.sentence_cases[1:])))


def test_a_case_naming_no_marker_is_rejected(dataset):
    broken = replace(dataset.sentence_cases[0], markers=())
    with pytest.raises(DatasetError, match="names no marker"):
        validate(replace(dataset, sentence_cases=(broken, *dataset.sentence_cases[1:])))


def test_a_marker_outside_the_vocabulary_is_rejected(dataset):
    broken = replace(dataset.sentence_cases[0], markers=("definitely-not-a-marker",))
    with pytest.raises(DatasetError, match="not in the vocabulary"):
        validate(replace(dataset, sentence_cases=(broken, *dataset.sentence_cases[1:])))


def test_an_unreviewable_rationale_is_rejected(dataset):
    broken = replace(dataset.sentence_cases[0], rationale="no")
    with pytest.raises(DatasetError, match="rationale"):
        validate(replace(dataset, sentence_cases=(broken, *dataset.sentence_cases[1:])))


def test_a_lopsided_pair_is_rejected():
    """Two cases sharing a pair_id that expect the SAME outcome are not a
    contrast, and a benchmark that let that pass would not be testing
    anything by pairing them."""
    a = SentenceCase(
        id="x1", category="genuine_non_doing", markers=("evaluated",), construction="positive",
        text="Evaluated Foo.", expected_non_doing=True, rationale="a genuine disclaimer sentence",
        pair_id="p1",
    )
    b = SentenceCase(
        id="x2", category="genuine_non_doing", markers=("evaluated",), construction="positive",
        text="Evaluated Bar.", expected_non_doing=True, rationale="another disclaimer sentence",
        pair_id="p1",
    )
    dataset = Dataset(
        version="test", review=_review(), sentence_cases=(a, b), pipeline_cases=()
    )
    with pytest.raises(DatasetError, match="not a contrast"):
        validate(dataset)


def test_a_pipeline_case_with_no_experiences_is_rejected(dataset):
    broken = replace(dataset.pipeline_cases[0], experiences=())
    with pytest.raises(DatasetError, match="no experiences"):
        validate(replace(dataset, pipeline_cases=(broken, *dataset.pipeline_cases[1:])))


def test_a_duration_requirement_with_no_minimum_years_is_rejected(dataset):
    case = dataset.pipeline_cases[0]
    broken = replace(case, requirement=replace(case.requirement, kind="duration", minimum_years=None))
    with pytest.raises(DatasetError, match="no minimum_years"):
        validate(replace(dataset, pipeline_cases=(broken, *dataset.pipeline_cases[1:])))


def test_an_unknown_expected_tier_is_rejected(dataset):
    case = dataset.pipeline_cases[0]
    broken_expected = replace(
        case.expected, tokens=(TokenExpectation(token="x", attributed_employment=True, tier="vibes"),)
    )
    broken = replace(case, expected=broken_expected)
    with pytest.raises(DatasetError, match="unknown expected tier"):
        validate(replace(dataset, pipeline_cases=(broken, *dataset.pipeline_cases[1:])))


def _review():
    from evaluation.nondoing.schema import ReviewMetadata

    return ReviewMetadata(created="x", reviewer="x", methodology="x", independence_statement="x")


# =============================================================================
# Metrics are arithmetically right
# =============================================================================


def test_a_rate_with_nothing_to_measure_is_not_zero():
    assert Rate(0, 0).value is None
    assert Rate(3, 4).value == 0.75


def test_false_positive_and_negative_are_opposite_directions():
    case = SentenceCase(
        id="x", category="genuine_non_doing", markers=("evaluated",), construction="positive",
        text="t", expected_non_doing=True, rationale="a rationale long enough to pass review",
    )
    false_negative = SentenceObservation(case=case, actual_non_doing=False)
    assert false_negative.is_false_negative
    assert not false_negative.is_false_positive

    other = replace(case, expected_non_doing=False)
    false_positive = SentenceObservation(case=other, actual_non_doing=True)
    assert false_positive.is_false_positive
    assert not false_positive.is_false_negative


def test_pipeline_observation_reports_the_first_divergent_stage(dataset):
    case = next(c for c in dataset.pipeline_cases if c.category == "technology_attribution")
    from evaluation.nondoing.metrics import ActualToken

    wrong_tokens = {
        expectation.token: ActualToken(attributed_employment=False, tier="mentioned")
        for expectation in case.expected.tokens
    }
    observation = PipelineObservation(
        case=case,
        actual_tokens=wrong_tokens,
        actual_resolved_subject=case.expected.resolved_subject,
        actual_duration_years=None,
        actual_duration_uncertain=True,
        actual_verdict=case.expected.verdict,
    )
    assert not observation.agrees
    assert observation.first_divergent_stage == "1-2:non_doing/attribution"


def test_defect_breakdown_separates_confirmed_from_resilient():
    case_confirmed = SentenceCase(
        id="a", category="noun_collision", markers=("keen",), construction="negative",
        text="t", expected_non_doing=False, rationale="a rationale long enough to pass review",
        defect_class="vocabulary",
    )
    case_resilient = replace(case_confirmed, id="b")
    report = Report(dataset_version="x", algorithm_version="y")
    report.sentence_observations = [
        SentenceObservation(case=case_confirmed, actual_non_doing=True),  # disagrees: confirmed
        SentenceObservation(case=case_resilient, actual_non_doing=False),  # agrees: resilient
    ]
    breakdown = report.defect_breakdown()
    assert breakdown["vocabulary"]["predicted_and_confirmed"] == 1
    assert breakdown["vocabulary"]["predicted_but_correct"] == 1


def test_a_control_that_disagrees_is_a_surprise():
    case = SentenceCase(
        id="a", category="noun_collision", markers=("keen",), construction="negative",
        text="t", expected_non_doing=False, rationale="a rationale long enough to pass review",
        defect_class="none",
    )
    report = Report(dataset_version="x", algorithm_version="y")
    report.sentence_observations = [SentenceObservation(case=case, actual_non_doing=True)]
    assert len(report.surprises) == 1
    assert report.defect_breakdown()["none"]["unpredicted_surprise"] == 1


# =============================================================================
# Running: reproducibility and reporting
# =============================================================================


def test_two_separate_runs_produce_identical_observations():
    first, second = runner.run(), runner.run()
    assert [o.actual_non_doing for o in first.report.sentence_observations] == [
        o.actual_non_doing for o in second.report.sentence_observations
    ]
    assert first.report.overall_agreement.value == second.report.overall_agreement.value


def test_the_run_records_the_algorithm_version(result):
    assert result.report.algorithm_version
    assert result.report.dataset_version == groundtruth.DEFAULT_VERSION


def test_the_benchmark_finds_disagreements(result):
    """A dataset this size that agreed everywhere would be indistinguishable
    from one copied off the implementation - see the independence tests."""
    assert result.report.overall_agreement.value is not None
    assert result.report.overall_agreement.value < 1.0


def test_the_text_report_renders_every_section(result):
    text = runner.report_text(result, show_failures=True)
    for heading in (
        "OVERALL AGREEMENT",
        "PER-CATEGORY ACCURACY",
        "PER-MARKER ACCURACY",
        "TIER CHANGES",
        "DURATION CHANGES",
        "MINIMAL-PAIR FAILURES",
        "TECHNOLOGY-ATTRIBUTION FAILURES",
        "TITLE-PATH FAILURES",
        "AWAY CONSTRUCTION FAILURES",
        "DEFECT CLASSIFICATION",
        "SURPRISES",
        "DETAILED DISAGREEMENTS",
    ):
        assert heading in text, heading


def test_the_json_report_carries_every_measure(result):
    import json

    payload = json.loads(runner.report_json(result))
    assert payload["dataset_version"] == groundtruth.DEFAULT_VERSION
    assert set(payload["agreement"]) == {"sentence", "pipeline", "overall"}
    assert set(payload["safety"]) == {"false_positives", "false_negatives"}
    assert payload["by_category"]
    assert payload["by_marker"]
    assert "defect_breakdown" in payload
