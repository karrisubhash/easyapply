"""The matching evaluation framework.

What is asserted here is the FRAMEWORK, never a score — the same line
`test_resume_evaluation.py` draws and for the same reason. A threshold checked
into CI before anyone knows what a good score looks like freezes today's
accident into tomorrow's requirement, and the first rule change that fixed
nine cases and cost one would go red for the wrong reason.

So: that the dataset covers the shapes §22 names, that scoring can tell a right
answer from a wrong one, and — the measure that matters most — that it counts a
false MATCHED as its own kind of error rather than as one wrong answer among
many.
"""

import pytest

from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.matching.engine import ALGORITHM_VERSION
from evaluation.match_runner import (
    CaseScore,
    RequirementOutcome,
    as_candidate,
    as_profile_version,
    load_cases,
    report,
    run,
    score_case,
)

#: The shapes §22 asks the dataset to cover. Coverage of awkwardness, not of
#: volume: a hundred perfect matches would score well and teach nothing.
REQUIRED_SHAPES = {
    "perfect skill match",
    "missing required skill",
    "missing preferred skill",
    "required skill matched through a supported alias",
    "similar-looking but NOT equivalent technologies",
    "experience clearly sufficient",
    "experience clearly insufficient",
    "experience uncertain — insufficient evidence, not absent evidence",
    "overlapping employment periods and a total-employment requirement",
    "education exact",
    "education mismatch — right level, unrelated field",
    "certification exact and supported variant",
    "certification mismatch — same vendor, different credential",
    "sparse profile against a full posting",
    "rich profile, many requirements, every verdict represented",
}


@pytest.fixture(scope="module")
def dataset():
    return load_cases()


@pytest.fixture(scope="module")
def scores():
    return run()


# --- the dataset --------------------------------------------------------------


def test_the_dataset_covers_the_shapes_the_milestone_names(dataset):
    shapes = {case["shape"] for case in dataset["cases"]}
    assert REQUIRED_SHAPES <= shapes


def test_every_case_is_well_formed(dataset):
    seen = set()
    for case in dataset["cases"]:
        assert case["id"] not in seen, f"duplicate case id {case['id']}"
        seen.add(case["id"])
        assert case["note"], f"{case['id']} does not say what it is for"
        assert case["as_of"], "a case without an as_of date is unreproducible"
        assert case["expected"], f"{case['id']} pins no expectations"


def test_every_expectation_names_a_real_verdict(dataset):
    for case in dataset["cases"]:
        for expectation in case["expected"]:
            assert expectation["status"] in {
                STATUS_MATCHED,
                STATUS_MISSING,
                STATUS_UNCERTAIN,
            }
            assert expectation["category"]
            assert expectation["label"]


def test_the_dataset_expects_all_three_verdicts(dataset):
    """A dataset that only ever expected MATCHED would be satisfied by a
    matcher that said yes to everything."""
    statuses = {
        expectation["status"]
        for case in dataset["cases"]
        for expectation in case["expected"]
    }
    assert statuses == {STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN}


def test_the_dataset_covers_every_checkable_category(dataset):
    categories = {
        expectation["category"]
        for case in dataset["cases"]
        for expectation in case["expected"]
    }
    assert categories == {
        "skill",
        "technology",
        "experience",
        "education",
        "certification",
        "qualification",
    }


def test_every_pinned_requirement_is_actually_judged(scores):
    """A case that names a requirement the engine never evaluates is a case
    that silently stopped testing anything."""
    for score in scores:
        assert score.unjudged == [], f"{score.case_id}: {score.unjudged}"


# --- the builders -------------------------------------------------------------


def test_a_case_profile_becomes_a_usable_profile_version(dataset):
    case = next(c for c in dataset["cases"] if c["id"] == "rich-profile-multiple-requirements")
    version = as_profile_version(case["profile"])

    assert version.status == "confirmed"
    assert len(version.skills) == 3
    assert len(version.experiences) == 2
    assert version.projects[0].technologies


def test_a_case_job_becomes_a_usable_candidate(dataset):
    case = next(c for c in dataset["cases"] if c["id"] == "rich-profile-multiple-requirements")
    candidate = as_candidate(case["job"])

    assert [item.name for item in candidate.required_skills] == [
        "Python",
        "PostgreSQL",
        "Django",
    ]
    assert candidate.technologies[0].is_required is True
    assert candidate.technologies[1].is_required is False


# --- the scoring can tell right from wrong ------------------------------------


def outcome(expected, actual):
    return RequirementOutcome(
        category="skill",
        label="Python",
        is_required=True,
        expected=expected,
        actual=actual,
        has_evidence=actual == STATUS_MATCHED,
    )


def test_a_correct_verdict_scores_as_correct():
    assert outcome(STATUS_MATCHED, STATUS_MATCHED).correct
    assert not outcome(STATUS_MATCHED, STATUS_MISSING).correct


def test_a_false_match_is_counted_as_its_own_error():
    """The measure that matters most. A MATCHED the case says should not be
    there is invisible to the person reading the result — they have no reason
    to check it — so it cannot be scored as merely one wrong answer."""
    wrong = outcome(STATUS_MISSING, STATUS_MATCHED)

    assert wrong.false_match
    assert not wrong.correct
    assert not outcome(STATUS_MATCHED, STATUS_MISSING).false_match


def test_a_false_missing_is_counted_separately_from_a_false_match():
    missed = outcome(STATUS_MATCHED, STATUS_MISSING)

    assert missed.false_missing
    assert not missed.false_match


def test_the_two_directions_of_misusing_uncertain_are_kept_apart():
    """Folding them together would hide which way the engine leans."""
    overcautious = outcome(STATUS_MATCHED, STATUS_UNCERTAIN)
    overconfident = outcome(STATUS_UNCERTAIN, STATUS_MATCHED)

    assert overcautious.overcautious and not overcautious.overconfident
    assert overconfident.overconfident and not overconfident.overcautious


def test_accuracy_is_correct_over_total():
    score = CaseScore(case_id="x", shape="x")
    score.outcomes = [
        outcome(STATUS_MATCHED, STATUS_MATCHED),
        outcome(STATUS_MATCHED, STATUS_MATCHED),
        outcome(STATUS_MATCHED, STATUS_MISSING),
        outcome(STATUS_MISSING, STATUS_MISSING),
    ]

    assert score.accuracy == 0.75
    assert score.false_missing == 1


def test_a_matched_verdict_without_evidence_fails_the_evidence_check():
    score = CaseScore(case_id="x", shape="x")
    score.outcomes = [
        RequirementOutcome(
            category="skill",
            label="Python",
            is_required=True,
            expected=STATUS_MATCHED,
            actual=STATUS_MATCHED,
            has_evidence=False,
        )
    ]

    assert not score.evidence_complete


# --- running the real dataset -------------------------------------------------


def test_every_case_runs_without_raising(scores):
    for score in scores:
        assert score.error is None, f"{score.case_id}: {score.error}"


def test_every_case_is_reproducible(scores):
    """§15, measured rather than asserted: the same inputs evaluated twice
    produce identical verdicts and an identical score."""
    for score in scores:
        assert score.reproducible, score.case_id


def test_the_score_always_agrees_with_its_own_verdicts(scores):
    """A scoring bug that disagreed with its inputs would be invisible in the
    headline accuracy."""
    for score in scores:
        assert score.score_consistent, score.case_id


def test_no_matched_verdict_anywhere_lacks_evidence(scores):
    for score in scores:
        assert score.evidence_complete, score.case_id


def test_the_near_miss_case_produces_no_false_matches(scores):
    """The one case worth pinning a number on, because the number is zero and
    §7 says it must stay zero. React Native is not React; Django is not
    Python. A future alias that broke this would be caught here."""
    case = next(score for score in scores if score.case_id == "similar-but-not-equivalent")

    assert case.false_matches == 0
    assert case.accuracy == 1.0


def test_the_report_names_the_algorithm_it_measured(scores):
    """A report that did not say which rules produced it would be
    uninterpretable the moment matching_v2 exists."""
    text = report(scores)

    assert ALGORITHM_VERSION in text
    assert "classification accuracy" in text
    assert "false matches" in text
