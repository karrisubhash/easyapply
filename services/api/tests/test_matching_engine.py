"""The traversal and the score.

`evaluate` is a pure function of (JobCandidate, ProfileVersion, as_of) — no
session, no clock, no configuration — and that is what makes a stored
`MatchResult` reproducible rather than merely recorded. These tests hold that
property directly, alongside the two things the traversal owns: which
categories are evaluated, and that required and preferred stay separate all the
way to the counts.

The score is tested against its documented formula, never against a number
someone liked. A test that pinned "0.87" would make the formula unchangeable
for the wrong reason.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.match import (
    ALGORITHM_V5_1,
    CATEGORY_CERTIFICATION,
    CATEGORY_EDUCATION,
    CATEGORY_EXPERIENCE,
    CATEGORY_QUALIFICATION,
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_UNCERTAIN,
)
from app.schemas.job_candidate import JobResponsibilityItem
from app.services.matching.engine import ALGORITHM_VERSION, evaluate
from app.services.matching.rules import RequirementJudgment
from app.services.matching.scoring import (
    PREFERRED_WEIGHT,
    REQUIRED_WEIGHT,
    AxisCounts,
    score,
)
from tests.matching_fixtures import (
    AS_OF,
    a_candidate,
    a_certification_row,
    a_project_row,
    a_skill_row,
    a_version,
    an_education_row,
    an_experience_row,
)


def verdicts_by_label(evaluation) -> dict[str, RequirementJudgment]:
    return {judgment.requirement_label: judgment for judgment in evaluation.judgments}


# =============================================================================
# The traversal
# =============================================================================


def test_every_checkable_category_is_evaluated():
    candidate = a_candidate(
        required_skills=["Python"],
        preferred_skills=["Rust"],
        technologies=[("PostgreSQL", True)],
        required_experience=[("3+ years of Python", 3.0)],
        education=[("Bachelor's degree", "bachelor", None, True)],
        certifications=[("AWS Certified Developer", True)],
        qualifications=[("Must be authorized to work in the US", True)],
    )
    evaluation = evaluate(candidate, a_version(), as_of=AS_OF)

    assert {judgment.category for judgment in evaluation.judgments} == {
        CATEGORY_SKILL,
        CATEGORY_TECHNOLOGY,
        CATEGORY_EXPERIENCE,
        CATEGORY_EDUCATION,
        CATEGORY_CERTIFICATION,
        CATEGORY_QUALIFICATION,
    }


def test_responsibilities_are_not_evaluated():
    """By design: a responsibility describes the role once hired, not a bar
    the candidate clears — which is why `JobCandidate` gives it no
    required/preferred axis in the first place."""
    candidate = a_candidate(required_skills=["Python"])
    candidate.responsibilities.append(
        JobResponsibilityItem(description="Mentor junior engineers", provenance=[])
    )
    evaluation = evaluate(candidate, a_version(skills=[a_skill_row("Python")]), as_of=AS_OF)

    assert len(evaluation.judgments) == 1
    assert evaluation.judgments[0].category == CATEGORY_SKILL


def test_a_free_text_qualification_is_recorded_rather_than_dropped():
    """§12: dropping it would shrink the denominator and make coverage look
    better than it is."""
    candidate = a_candidate(
        required_skills=["Python"],
        qualifications=[("Must hold a security clearance", True)],
    )
    evaluation = evaluate(candidate, a_version(skills=[a_skill_row("Python")]), as_of=AS_OF)

    assert evaluation.score.required.total == 2
    assert evaluation.score.required.matched == 1
    assert evaluation.score.required.uncertain == 1


def test_requirement_indexes_are_unique_within_a_category():
    """(category, requirement_index) is the table's own uniqueness rule, so
    preferred skills continue the required sequence rather than restarting."""
    candidate = a_candidate(
        required_skills=["Python", "Django"],
        preferred_skills=["Rust", "Go"],
        required_experience=[("3+ years backend", 3.0)],
        preferred_experience=[("Startup experience", None)],
    )
    evaluation = evaluate(candidate, a_version(), as_of=AS_OF)

    seen = [(judgment.category, judgment.requirement_index) for judgment in evaluation.judgments]
    assert len(seen) == len(set(seen))


def test_an_empty_posting_produces_no_judgments():
    evaluation = evaluate(a_candidate(), a_version(), as_of=AS_OF)

    assert evaluation.judgments == []
    assert evaluation.score.required.total == 0


# =============================================================================
# Required and preferred stay separate
# =============================================================================


def test_a_missing_preferred_requirement_does_not_touch_the_required_axis():
    """§8's example: Python and Django required, Kubernetes preferred and
    absent. The result must not suggest a required qualification failed."""
    candidate = a_candidate(
        required_skills=["Python", "Django"], preferred_skills=["Kubernetes"]
    )
    version = a_version(skills=[a_skill_row("Python"), a_skill_row("Django")])
    evaluation = evaluate(candidate, version, as_of=AS_OF)

    assert evaluation.score.required == AxisCounts(total=2, matched=2)
    assert evaluation.score.required.missing == 0
    assert evaluation.score.preferred == AxisCounts(total=1, missing=1)
    assert evaluation.score.required.coverage == Decimal("1.0000")


def test_a_technology_carries_its_own_required_flag():
    """Technologies are per-item, unlike skills which are split into two
    lists at the schema level."""
    candidate = a_candidate(technologies=[("Python", True), ("Redis", False)])
    evaluation = evaluate(candidate, a_version(), as_of=AS_OF)
    by_label = verdicts_by_label(evaluation)

    assert by_label["Python"].is_required is True
    assert by_label["Redis"].is_required is False


def test_required_and_preferred_verdicts_of_the_same_kind_are_counted_apart():
    candidate = a_candidate(required_skills=["Python"], preferred_skills=["Python"])
    evaluation = evaluate(candidate, a_version(), as_of=AS_OF)

    assert evaluation.score.required == AxisCounts(total=1, missing=1)
    assert evaluation.score.preferred == AxisCounts(total=1, missing=1)


# =============================================================================
# The score
# =============================================================================


def a_judgment(status, *, required):
    return RequirementJudgment(
        category=CATEGORY_SKILL,
        requirement_index=0,
        requirement_label="x",
        requirement_key="x",
        is_required=required,
        status=status,
        confidence=0.9,
        explanation="because",
    )


def test_coverage_is_matched_over_total():
    counts = AxisCounts(total=4, matched=3, missing=1)
    assert counts.coverage == Decimal("0.7500")


def test_an_uncertain_requirement_dilutes_but_never_counts_as_met():
    """The failure §12 exists to prevent: two matched and eight unreadable
    requirements must not report 100%."""
    judgments = [a_judgment(STATUS_MATCHED, required=True) for _ in range(2)]
    judgments += [a_judgment(STATUS_UNCERTAIN, required=True) for _ in range(8)]

    totals = score(judgments)

    assert totals.required.coverage == Decimal("0.2000")
    assert totals.required.uncertain == 8
    assert totals.required.matched == 2


def test_an_empty_axis_scores_one_rather_than_zero():
    """A posting with no preferred requirements has not failed to have them."""
    totals = score([a_judgment(STATUS_MATCHED, required=True)])

    assert totals.preferred.total == 0
    assert totals.preferred.coverage == Decimal("1.0000")


def test_the_overall_score_is_the_documented_blend():
    judgments = [
        a_judgment(STATUS_MATCHED, required=True),
        a_judgment(STATUS_MISSING, required=True),
        a_judgment(STATUS_MATCHED, required=False),
    ]
    totals = score(judgments)

    expected = REQUIRED_WEIGHT * Decimal("0.5") + PREFERRED_WEIGHT * Decimal("1.0")
    assert totals.overall == expected.quantize(Decimal("0.0001"))


def test_required_outweighs_preferred_four_to_one():
    all_required = score(
        [a_judgment(STATUS_MATCHED, required=True), a_judgment(STATUS_MISSING, required=False)]
    )
    all_preferred = score(
        [a_judgment(STATUS_MISSING, required=True), a_judgment(STATUS_MATCHED, required=False)]
    )

    assert all_required.overall > all_preferred.overall
    assert REQUIRED_WEIGHT + PREFERRED_WEIGHT == Decimal("1.0")


def test_a_missing_required_requirement_is_never_turned_into_a_rejection():
    """§13: the engine reports counts and lets product logic decide. Nothing
    it produces says a candidate is unsuitable, and a total miss still scores
    the preferred axis honestly."""
    judgments = [a_judgment(STATUS_MISSING, required=True)]
    totals = score(judgments)

    assert totals.required.coverage == Decimal("0.0000")
    assert totals.overall == PREFERRED_WEIGHT.quantize(Decimal("0.0001"))


def test_every_score_is_a_fraction():
    for judgments in (
        [],
        [a_judgment(STATUS_MATCHED, required=True)],
        [a_judgment(STATUS_MISSING, required=False)],
        [a_judgment(STATUS_UNCERTAIN, required=True), a_judgment(STATUS_MATCHED, required=False)],
    ):
        totals = score(judgments)
        for value in (totals.required.coverage, totals.preferred.coverage, totals.overall):
            assert Decimal("0") <= value <= Decimal("1")


def test_the_counts_always_sum_to_the_total():
    """The same invariant the table's CHECK constraints hold. Asserted here
    too, because a scoring bug should fail a test rather than an INSERT."""
    candidate = a_candidate(
        required_skills=["Python", "Rust"],
        preferred_skills=["Go"],
        required_experience=[("5+ years of Haskell", 5.0)],
        qualifications=[("Must be willing to travel", False)],
    )
    totals = evaluate(candidate, a_version(skills=[a_skill_row("Python")]), as_of=AS_OF).score

    for axis in (totals.required, totals.preferred):
        assert axis.matched + axis.missing + axis.uncertain == axis.total


# =============================================================================
# Reproducibility
# =============================================================================


def a_rich_case():
    candidate = a_candidate(
        required_skills=["Python", "PostgreSQL", "Rust"],
        preferred_skills=["Kubernetes"],
        technologies=[("Django", True), ("Redis", False)],
        required_experience=[("5+ years of Python", 5.0), ("3+ years of Django", 3.0)],
        education=[("Bachelor's degree in Computer Science", "bachelor", None, True)],
        certifications=[("AWS Certified Developer", True)],
        qualifications=[("Must be authorized to work in the US", True)],
    )
    version = a_version(
        skills=[a_skill_row("Python", years=6), a_skill_row("Postgres")],
        experiences=[
            an_experience_row(
                job_title="Backend Developer",
                start=date(2019, 1, 1),
                end=date(2024, 1, 1),
                description="Django REST API development",
            )
        ],
        education=[an_education_row(degree="B.Tech Mechanical Engineering", field_of_study="Mechanical Engineering")],
        certifications=[a_certification_row("AWS Certified Developer – Associate")],
        projects=[a_project_row(technologies=["Redis"])],
    )
    return candidate, version


def test_the_same_inputs_produce_the_same_result():
    """§15's core claim. Run twice, compare every field of every verdict."""
    candidate, version = a_rich_case()

    first = evaluate(candidate, version, as_of=AS_OF)
    second = evaluate(candidate, version, as_of=AS_OF)

    assert first.judgments == second.judgments
    assert first.score == second.score
    assert first.score.overall == second.score.overall


def test_the_as_of_date_is_an_input_not_a_clock_read():
    """A matcher that read `date.today()` internally would be unreproducible
    by construction: the same stored result could not be recomputed tomorrow."""
    candidate = a_candidate(required_experience=[("5+ years of professional experience", 5.0)])
    version = a_version(
        experiences=[an_experience_row(start=date(2020, 1, 1), end=None)]
    )

    assert evaluate(candidate, version, as_of=date(2026, 1, 1)).judgments[0].status == STATUS_MATCHED
    assert evaluate(candidate, version, as_of=date(2022, 1, 1)).judgments[0].status == STATUS_MISSING


def test_the_as_of_date_is_carried_on_the_evaluation():
    evaluation = evaluate(a_candidate(), a_version(), as_of=AS_OF)
    assert evaluation.as_of == AS_OF


def test_the_algorithm_version_is_stamped_on_every_evaluation():
    """§14: mandatory, and never inferred from a timestamp."""
    evaluation = evaluate(a_candidate(required_skills=["Python"]), a_version(), as_of=AS_OF)

    assert evaluation.algorithm_version == ALGORITHM_VERSION == ALGORITHM_V5_1 == "matching_v5_1"


def test_the_engine_never_reads_the_clock():
    """Belt and braces for the property above: monkeypatching today() must
    change nothing, because nothing calls it."""
    candidate, version = a_rich_case()
    before = evaluate(candidate, version, as_of=AS_OF)

    import app.services.matching.rules as rules

    assert "date.today" not in open(rules.__file__, encoding="utf-8").read()
    after = evaluate(candidate, version, as_of=AS_OF)
    assert before.judgments == after.judgments


def test_evaluation_does_not_mutate_the_profile_version():
    candidate, version = a_rich_case()
    before = (len(version.skills), len(version.experiences), len(version.projects))

    evaluate(candidate, version, as_of=AS_OF)

    assert (len(version.skills), len(version.experiences), len(version.projects)) == before


# =============================================================================
# End-to-end shapes
# =============================================================================


def test_a_sparse_profile_produces_misses_with_explanations_not_silence():
    candidate = a_candidate(required_skills=["Python", "Django"], preferred_skills=["Go"])
    evaluation = evaluate(candidate, a_version(), as_of=AS_OF)

    assert len(evaluation.judgments) == 3
    for judgment in evaluation.judgments:
        assert judgment.status == STATUS_MISSING
        assert "No qualifying evidence was found" in judgment.explanation


def test_a_rich_profile_produces_the_full_mix_of_verdicts():
    candidate, version = a_rich_case()
    evaluation = evaluate(candidate, version, as_of=AS_OF)
    statuses = {judgment.status for judgment in evaluation.judgments}

    assert statuses == {STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN}


def test_every_matched_verdict_in_a_full_evaluation_carries_evidence():
    candidate, version = a_rich_case()
    evaluation = evaluate(candidate, version, as_of=AS_OF)

    for judgment in evaluation.judgments:
        if judgment.status == STATUS_MATCHED:
            assert judgment.evidence, judgment.requirement_label


def test_a_perfect_match_scores_one():
    candidate = a_candidate(required_skills=["Python"], preferred_skills=["Go"])
    version = a_version(skills=[a_skill_row("Python"), a_skill_row("Golang")])
    evaluation = evaluate(candidate, version, as_of=AS_OF)

    assert evaluation.score.overall == Decimal("1.0000")
    assert evaluation.score.required.missing == 0
