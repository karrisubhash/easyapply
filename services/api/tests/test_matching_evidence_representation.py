"""The matching_v6 Phase 1 evidence representation.

Phase 1 changed what the index REMEMBERS, and nothing about what the engine
DECIDES. These tests are written to hold both halves of that claim at once:

  * the richer records exist, are populated for each arrangement the four-way
    tier flattens, and carry where the work happened;
  * `tier`, `has_any` and `best()` are exactly what they were, so every verdict
    matching_v5 reached is the verdict this code reaches.

The second half is the one that matters. A representation change that quietly
moved a verdict would be a policy change wearing a disguise, and the whole
point of doing this phase separately is that it cannot be one. `test_the_tier_
is_a_pure_function_of_the_three_original_lists` is the property test that says
so directly; the benchmark suites say it again over 263 real judgments
(`tests/test_declared_only_benchmarks.py`, and the four reports diffed in the
Phase 1 report).

No policy reads `corroborations` or `disclaimers`. That is deliberate and is
itself asserted here: if a future change makes a verdict depend on them, one of
these tests fails, and it should, because that change needs an
ALGORITHM_VERSION bump these tests do not cover.
"""

from __future__ import annotations

from datetime import date

from app.services.matching.evidence import (
    SETTING_EMPLOYMENT,
    SETTING_PROJECT,
    TIER_DECLARED_CORROBORATED,
    TIER_DECLARED_ONLY,
    TIER_EMPLOYMENT_DESCRIBED,
    TIER_MENTIONED,
    Evidence,
    TokenEvidence,
    graded,
    satisfies,
)
from app.services.matching.profile_index import ProfileIndex
from tests.matching_fixtures import (
    a_project_row,
    a_skill_row,
    a_version,
    an_experience_row,
)

AS_OF = date(2026, 1, 1)


def _index(**kwargs) -> ProfileIndex:
    return ProfileIndex(a_version(**kwargs))


# --- the arrangements the tier flattens ---------------------------------------


def test_a_bare_declaration_records_the_declaration_and_nothing_else():
    """S1. A skills row, and nothing anywhere else in the profile."""
    found = _index(skills=[a_skill_row("Kubernetes")]).evidence_for("kubernetes")

    assert [item.source_type for item in found.declarations] == ["skill"]
    assert found.corroborations == []
    assert found.disclaimers == []
    assert found.employment == []
    assert found.tier == TIER_DECLARED_ONLY


def test_a_project_that_describes_the_work_is_kept_as_corroboration():
    """S2. The information Phase 1 exists to stop discarding.

    Before Phase 1 the description sentence was dropped entirely, because the
    same project had already declared the technology — so this profile was
    indistinguishable from the one in the test above.
    """
    found = _index(
        skills=[a_skill_row("Docker")],
        projects=[
            a_project_row(
                name="Larkspur home automation",
                technologies=["Docker"],
                description="Deployed the whole platform with Docker Compose and have run it since 2023.",
            )
        ],
    ).evidence_for("docker")

    assert found.corroborations, "the project description was discarded, not recorded"
    assert [item.source_type for item in found.corroborations] == ["project"]
    assert "Docker Compose" in found.corroborations[0].quote
    assert found.disclaimers == []
    # And the projection did not move: the corroboration is unpaid, so the
    # profile still only DECLARES this technology.
    assert found.tier == TIER_DECLARED_ONLY


def test_a_project_that_says_the_work_was_not_done_is_kept_as_a_disclaimer():
    """S6. The sharpest form of the defect Phase 1 closes.

    This project declares Terraform and then says outright that it was never
    used. Before Phase 1 that sentence was suppressed — the profile graded
    exactly as if the description had never been written.
    """
    found = _index(
        skills=[a_skill_row("Terraform")],
        projects=[
            a_project_row(
                name="Nightsail sandbox",
                technologies=["Terraform"],
                description="I evaluated Terraform for it but ended up deploying everything by hand.",
            )
        ],
    ).evidence_for("terraform")

    assert found.disclaimers, "the contradiction was discarded"
    assert "evaluated Terraform" in found.disclaimers[0].quote
    assert found.corroborations == []
    assert found.tier == TIER_DECLARED_ONLY


def test_employment_corroboration_is_retained():
    """S3. Already representable before Phase 1, and still is."""
    found = _index(
        skills=[a_skill_row("Django")],
        experiences=[
            an_experience_row(
                job_title="Backend Engineer",
                description="Built Django REST services for a booking platform.",
                start=date(2020, 1, 1),
            )
        ],
    ).evidence_for("django")

    assert found.employment
    assert found.declarations
    assert found.tier == TIER_DECLARED_CORROBORATED


def test_prose_only_evidence_is_retained_for_a_role_and_for_a_project():
    """S4 and S5: described somewhere, declared nowhere."""
    role = _index(
        experiences=[
            an_experience_row(
                job_title="Data Engineer",
                description="Own the Airflow schedules behind the platform.",
                start=date(2020, 1, 1),
            )
        ]
    ).evidence_for("airflow")
    assert role.declarations == []
    assert role.employment, "prose-only employment evidence was lost"
    assert role.tier == TIER_EMPLOYMENT_DESCRIBED

    project = _index(
        projects=[
            a_project_row(
                name="Allotment planner",
                technologies=["Python"],
                description="It stores everything in Postgres and has done since the first season.",
            )
        ]
    ).evidence_for("postgresql")
    assert project.declarations == []
    assert project.corroborations, "prose-only project evidence was lost"
    assert project.mentions, "the mentions rule must be untouched for an undeclared token"
    assert project.tier == TIER_MENTIONED


def test_a_role_clause_that_denies_the_work_is_recorded_as_a_denial():
    """The employment half of the polarity split.

    `mentions` has always held this clause. What it could not say is that the
    clause is a denial rather than a neutral naming.
    """
    found = _index(
        skills=[a_skill_row("Kafka")],
        experiences=[
            an_experience_row(
                job_title="Backend Engineer",
                description="Evaluated Kafka for the ingest path but the project was cancelled.",
                start=date(2020, 1, 1),
            )
        ],
    ).evidence_for("kafka")

    assert found.mentions, "the mentions rule must be untouched"
    assert found.disclaimers, "the denial was not recorded as one"
    assert found.disclaimers[0].source_id == found.mentions[0].source_id
    assert found.employment == []


# --- setting: where the work happened ------------------------------------------


def test_setting_survives_extraction_for_each_kind_of_evidence():
    index = _index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Backend Engineer",
                description="Own the Python services behind reconciliation.",
                start=date(2020, 1, 1),
            )
        ],
        projects=[
            a_project_row(
                name="Side tool",
                technologies=["Redis"],
                description="A cache in front of the lookups, running on Redis since 2023.",
            )
        ],
    )

    python = index.evidence_for("python")
    skill_row = next(item for item in python.declarations if item.source_type == "skill")
    assert skill_row.setting is None, "a skills row asserts a skill, not a place"
    assert python.employment[0].setting == SETTING_EMPLOYMENT

    redis = index.evidence_for("redis")
    declaration = next(
        item for item in redis.declarations if item.source_type == "project_technology"
    )
    assert declaration.setting == SETTING_PROJECT
    assert redis.corroborations[0].setting == SETTING_PROJECT


def test_grading_evidence_keeps_its_setting():
    """`graded()` restamps the tier; it must not drop the rest."""
    original = Evidence(
        source_type="experience",
        source_id=7,
        label="Experience: Backend Engineer at Acme",
        quote="Own the Python services.",
        setting=SETTING_EMPLOYMENT,
    )
    assert graded(original, TIER_EMPLOYMENT_DESCRIBED).setting == SETTING_EMPLOYMENT


# --- the projection is unchanged -------------------------------------------------


def test_the_tier_is_a_pure_function_of_the_three_original_lists():
    """The property that makes Phase 1 verdict-neutral rather than merely
    verdict-neutral-so-far.

    Every combination of the three original lists is checked against the tier
    matching_v5 assigned it, with the two new lists filled arbitrarily. If a
    later change lets `corroborations` or `disclaimers` reach the tier, this
    fails — which is the point.
    """
    item = Evidence(source_type="skill", source_id=1, label="Skill: Go")
    noise = [Evidence(source_type="project", source_id=2, label="Project: Anything")]

    expected = {
        (True, True, True): TIER_DECLARED_CORROBORATED,
        (True, True, False): TIER_DECLARED_CORROBORATED,
        (True, False, True): TIER_DECLARED_ONLY,
        (True, False, False): TIER_DECLARED_ONLY,
        (False, True, True): TIER_EMPLOYMENT_DESCRIBED,
        (False, True, False): TIER_EMPLOYMENT_DESCRIBED,
        (False, False, True): TIER_MENTIONED,
        (False, False, False): None,
    }

    for (has_declaration, has_employment, has_mention), tier in expected.items():
        for corroborations, disclaimers in (([], []), (list(noise), list(noise))):
            found = TokenEvidence(
                declarations=[item] if has_declaration else [],
                employment=[item] if has_employment else [],
                mentions=[item] if has_mention else [],
                corroborations=corroborations,
                disclaimers=disclaimers,
            )
            assert found.tier == tier
            assert found.has_any is any((has_declaration, has_employment, has_mention))


def test_the_new_lists_do_not_reach_has_any_or_best():
    """A token known ONLY through the new lists must still read as unknown.

    This cannot arise from the current index — every corroboration and every
    disclaimer is filed beside an entry in one of the original three — but the
    rules ask `has_any` before anything else, and a change that let the new
    lists answer it would turn a MISSING into an UNCERTAIN without anyone
    choosing that.
    """
    stray = [Evidence(source_type="project", source_id=3, label="Project: Orphan")]
    found = TokenEvidence(corroborations=list(stray), disclaimers=list(stray))

    assert found.tier is None
    assert found.has_any is False
    assert found.best() == []


def test_satisfies_is_unchanged_for_every_tier_and_standard():
    """The sufficiency policy is matching_v5's, exactly. Phase 1 does not
    touch it, and no V6 policy exists."""
    expected = {
        (TIER_DECLARED_CORROBORATED, "any_evidence"): True,
        (TIER_EMPLOYMENT_DESCRIBED, "any_evidence"): True,
        (TIER_DECLARED_ONLY, "any_evidence"): True,
        (TIER_MENTIONED, "any_evidence"): False,
        (None, "any_evidence"): False,
        (TIER_DECLARED_CORROBORATED, "professional_use"): True,
        (TIER_EMPLOYMENT_DESCRIBED, "professional_use"): True,
        (TIER_DECLARED_ONLY, "professional_use"): False,
        (TIER_MENTIONED, "professional_use"): False,
        (None, "professional_use"): False,
    }
    for (tier, standard), outcome in expected.items():
        assert satisfies(tier, standard) is outcome, f"{tier} / {standard}"


def test_the_suppression_rule_itself_is_untouched():
    """A project that declares a technology still contributes no MENTION for
    it — the de-duplication matching_v5 introduced is intact. Phase 1 changed
    where the sentence goes, not whether it counts twice."""
    found = _index(
        projects=[
            a_project_row(
                name="Larkspur",
                technologies=["Docker"],
                description="Deployed with Docker Compose from the start.",
            )
        ]
    ).evidence_for("docker")

    assert found.mentions == [], "the suppressed sentence leaked back into mentions"
    assert found.corroborations, "...and was not recorded anywhere else either"
    assert found.tier == TIER_DECLARED_ONLY


# --- the benchmarks matching_v5 was measured on ----------------------------------
#
# The property tests above say the tier cannot move. These say it did not, over
# every judgment the project has ever recorded a human opinion about. They are
# pins, not targets: if a later change moves them ON PURPOSE, update them in the
# same commit as the ALGORITHM_VERSION bump and say so in the release notes.


def test_the_real_data_benchmark_is_exactly_what_matching_v5_scored():
    from evaluation.benchmark import groundtruth as benchmark_groundtruth
    from evaluation.benchmark.runner import run

    report = run(dataset_version="matching_real_v1").report
    # matching_v5_1 changed no verdict on this dataset — measured, not assumed;
    # the counts below are the pin, and they are matching_v5's.
    assert report.algorithm_version == "matching_v5_1"
    assert len(report.observations) == 169
    assert sum(1 for o in report.observations if o.agrees) == 154
    assert len(report.false_matches) == 4
    assert len(report.false_missing) == 3
    assert benchmark_groundtruth.load("matching_real_v1").requirement_count == 169


def test_the_non_doing_benchmark_still_expects_the_same_tiers():
    """`non_doing_v2` pins a tier and an attribution for 53 tokens.

    It is the only benchmark that observes the evidence REPRESENTATION rather
    than only the verdict, which makes it the sharpest available check that
    Phase 1 changed nothing a consumer can see.
    """
    from evaluation.nondoing.runner import run

    report = run().report
    assert report.sentence_agreement.value == 54 / 99
    assert report.pipeline_agreement.value == 33 / 40

    # Four tokens where the dataset's expectation and the matcher disagree, all
    # of them pre-existing and all four documented as known limitations in
    # docs/matching-v5-release.md §7: pl-012/pl-017/pl-035 are the
    # "familiar/curious + intensifier" vocabulary defect, pl-028 is the accepted
    # cost of matching_v5's same-clause precedence safeguard. Phase 1 must add
    # none and remove none — it is a representation change, and closing a
    # vocabulary defect is not its job.
    assert sorted(
        (observation.case.id, token, expected, actual)
        for observation, token, expected, actual in report.tier_changes
    ) == [
        ("pl-012", "snowflake", "employment_described", "mentioned"),
        ("pl-017", "terraform", "declared_corroborated", "declared_only"),
        ("pl-028", "graphql", "employment_described", "mentioned"),
        ("pl-035", "tableau", "declared_corroborated", "declared_only"),
    ]


def test_a_chatty_description_is_still_one_piece_of_evidence():
    """Repetition must not read as corroboration, in the new lists either."""
    found = _index(
        skills=[a_skill_row("Docker")],
        projects=[
            a_project_row(
                name="Larkspur",
                technologies=["Docker"],
                description=(
                    "Built it with Docker. Ran it on Docker for two years. "
                    "Still deploying with Docker today."
                ),
            )
        ],
    ).evidence_for("docker")

    assert len(found.corroborations) == 1
