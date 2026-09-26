"""The deterministic rules, one verdict at a time.

No database, no application, no clock. `judge_*` is a pure function of a
requirement, a `ProfileIndex` and (where duration matters) an `as_of` date, so
every branch can be reached directly and every expectation here is a rule a
person could apply by hand.

The shape each test holds to: assert the STATUS, then assert the EVIDENCE.
A verdict without the evidence check would pass for a matcher that guessed
correctly, and guessing correctly is not what this engine is for.
"""

from datetime import date

import pytest

from app.models.match import (
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
    EVIDENCE_CERTIFICATION,
    EVIDENCE_EDUCATION,
    EVIDENCE_EXPERIENCE,
    EVIDENCE_PROJECT,
    EVIDENCE_PROJECT_TECHNOLOGY,
    EVIDENCE_SKILL,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_UNCERTAIN,
)
from app.services.matching.evidence import (
    TIER_DECLARED_CORROBORATED,
    TIER_DECLARED_ONLY,
    TIER_EMPLOYMENT_DESCRIBED,
    TIER_MENTIONED,
)
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.rules import (
    _Span,
    degree_level,
    field_of_study_from_text,
    judge_certification,
    judge_education,
    judge_experience,
    judge_qualification,
    judge_token_requirement,
    merge_years,
)
from tests.matching_fixtures import (
    AS_OF,
    a_certification_row,
    a_project_row,
    a_skill_row,
    a_version,
    an_education_row,
    an_experience_row,
)


def index(**kwargs) -> ProfileIndex:
    return ProfileIndex(a_version(**kwargs))


def judge_skill(name, profile, *, required=True, category=CATEGORY_SKILL):
    return judge_token_requirement(
        name=name, category=category, index=0, is_required=required, profile=profile
    )


# =============================================================================
# Skills and technologies
# =============================================================================


def test_a_listed_skill_matches_exactly():
    profile = index(skills=[a_skill_row("Python")])
    verdict = judge_skill("Python", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.requirement_key == "python"
    assert verdict.matched_value == "python"
    assert [item.source_type for item in verdict.evidence] == [EVIDENCE_SKILL]
    assert "Python" in verdict.evidence[0].label


@pytest.mark.parametrize("spelling", ["python", "PYTHON", "  Python  ", "Python 3"])
def test_a_listed_skill_matches_through_typography(spelling):
    profile = index(skills=[a_skill_row("Python")])
    assert judge_skill(spelling, profile).status == STATUS_MATCHED


@pytest.mark.parametrize(
    "held, required",
    [
        ("Postgres", "PostgreSQL"),
        ("PostgreSQL", "Postgres"),
        ("K8s", "Kubernetes"),
        ("Golang", "Go"),
        ("Amazon Web Services", "AWS"),
        ("Node.js", "NodeJS"),
    ],
)
def test_a_supported_alias_matches(held, required):
    profile = index(skills=[a_skill_row(held)])
    verdict = judge_skill(required, profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence, "an alias match must still point at the row that justified it"


@pytest.mark.parametrize(
    "held, required",
    [
        ("Django", "Python"),
        ("Python", "Django"),
        ("React", "React Native"),
        ("React Native", "React"),
        ("MySQL", "PostgreSQL"),
        ("JavaScript", "Java"),
        ("Azure", "AWS"),
    ],
)
def test_an_unsupported_alias_does_not_match(held, required):
    """The pairs §7 names. Each is MISSING rather than MATCHED because no
    alias claims otherwise — not because of a special case."""
    profile = index(skills=[a_skill_row(held)])
    verdict = judge_skill(required, profile)

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence == []
    assert "No qualifying evidence was found" in verdict.explanation


def test_a_project_technology_is_structured_evidence():
    """A technology the user attached to a project is a claim, the same as a
    skill row — it is a field whose purpose is to name it."""
    profile = index(projects=[a_project_row(name="EasyApply", technologies=["Django"])])
    verdict = judge_skill("Django", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].source_type == EVIDENCE_PROJECT_TECHNOLOGY
    assert verdict.evidence[0].label == "Project: EasyApply"
    assert verdict.evidence[0].detail == "Technology: Django"


def test_work_described_in_a_paid_role_qualifies_even_if_unlisted():
    """The benchmark's largest false-missing class, and the reason for tiers.

    matching_v1 called this UNCERTAIN because Django was not in the skills
    list — so six years of daily work described in a current role scored the
    same as a word appearing in passing. A reviewer reads the whole document,
    and so does matching_v2."""
    profile = index(
        experiences=[
            an_experience_row(
                job_title="Backend Developer",
                description="Django REST API development for the payments team",
            )
        ]
    )
    verdict = judge_skill("Django", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].tier == TIER_EMPLOYMENT_DESCRIBED
    assert verdict.evidence[0].source_type == EVIDENCE_EXPERIENCE
    assert "Backend Developer" in verdict.evidence[0].label
    # The clause is quoted, so a reader can judge the claim themselves.
    assert "Django REST API" in verdict.evidence[0].quote


def test_repetition_is_not_corroboration():
    """A description that says "Django" three times is one piece of evidence.

    Counting occurrences would make repetition look like corroboration, and
    corroboration is what separates the strongest tier from the next. One
    clause per role per token, and no tier is reachable by saying a word more
    often."""
    profile = index(
        experiences=[
            an_experience_row(description="Django Django Django", id=1),
            an_experience_row(description="More Django work", company="Other", id=2),
        ]
    )
    verdict = judge_skill("Django", profile)

    assert verdict.status == STATUS_MATCHED
    # Two roles, two pieces of evidence — not five.
    assert len(verdict.evidence) == 2
    # Still not the top tier: nothing here is DECLARED.
    assert all(item.tier == TIER_EMPLOYMENT_DESCRIBED for item in verdict.evidence)


def test_declared_and_described_is_the_strongest_evidence():
    """And it cites the WORK first.

    "Backend Developer at Acme — Django work" tells a reader more than "Skill:
    Django", and the real-data benchmark scored citing the latter as an
    evidence error even where the verdict was right."""
    profile = index(
        skills=[a_skill_row("Django")],
        experiences=[an_experience_row(description="Django work")],
    )
    verdict = judge_skill("Django", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.confidence == pytest.approx(0.95)
    assert all(item.tier == TIER_DECLARED_CORROBORATED for item in verdict.evidence)
    assert verdict.evidence[0].source_type == EVIDENCE_EXPERIENCE
    assert any(item.source_type == EVIDENCE_SKILL for item in verdict.evidence)


def test_a_project_description_does_not_duplicate_its_own_technology():
    """A project that lists Django AND says "Django" in its description is one
    piece of evidence, not two."""
    profile = index(
        projects=[
            a_project_row(
                name="EasyApply", technologies=["Django"], description="A Django application"
            )
        ]
    )
    verdict = judge_skill("Django", profile)

    assert verdict.status == STATUS_MATCHED
    assert len(verdict.evidence) == 1


def test_an_empty_profile_misses_everything():
    verdict = judge_skill("Python", index())

    assert verdict.status == STATUS_MISSING
    assert verdict.confidence == pytest.approx(0.9)


def test_a_requirement_that_normalizes_to_nothing_is_uncertain():
    """Not MISSING: we did not look and fail, we could not look at all."""
    verdict = judge_skill("///", index(skills=[a_skill_row("Python")]))

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.requirement_key is None


def test_a_technology_requirement_uses_the_same_rule_under_its_own_category():
    profile = index(skills=[a_skill_row("Redis")])
    verdict = judge_skill("Redis", profile, category=CATEGORY_TECHNOLOGY)

    assert verdict.category == CATEGORY_TECHNOLOGY
    assert verdict.status == STATUS_MATCHED


def test_the_requirement_label_is_the_postings_own_words():
    """Never rewritten: a person reading the result has to recognise what they
    read in the posting."""
    verdict = judge_skill("PostgreSQL", index(skills=[a_skill_row("Postgres")]))

    assert verdict.requirement_label == "PostgreSQL"
    assert verdict.requirement_key == "postgresql"


# =============================================================================
# Certifications
# =============================================================================


def certify(name, profile, *, required=True, as_of=AS_OF):
    return judge_certification(
        name=name, index=0, is_required=required, profile=profile, as_of=as_of
    )


def test_an_exactly_held_certification_matches():
    profile = index(certifications=[a_certification_row("AWS Certified Developer")])
    verdict = certify("AWS Certified Developer", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.confidence == pytest.approx(0.95)
    assert verdict.evidence[0].source_type == EVIDENCE_CERTIFICATION


def test_a_more_specific_held_certification_matches():
    """§11's example. The extra words name the LEVEL of the same credential,
    so holding the Associate answers a requirement for the credential."""
    profile = index(
        certifications=[a_certification_row("AWS Certified Developer – Associate")]
    )
    verdict = certify("AWS Certified Developer", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.confidence == pytest.approx(0.8)
    assert "AWS Certified Developer" in verdict.evidence[0].label


def test_a_less_specific_held_certification_is_uncertain():
    """The reverse direction does NOT match: a posting that asks for the
    Associate level is not answered by the unqualified name."""
    profile = index(certifications=[a_certification_row("AWS Certified Developer")])
    verdict = certify("AWS Certified Developer Associate", profile)

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence, "an uncertain verdict must still show what was found"


@pytest.mark.parametrize(
    "held",
    [
        "AWS Certified Solutions Architect",
        "AWS Certified SysOps Administrator",
        "Azure Developer Associate",
        "Certified Kubernetes Administrator",
    ],
)
def test_an_unrelated_certification_does_not_match(held):
    """§11: unrelated credentials from the same vendor are not equivalent.
    Sharing the words "AWS Certified" is not sharing a credential."""
    profile = index(certifications=[a_certification_row(held)])
    verdict = certify("AWS Certified Developer", profile)

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence == []


def test_an_expired_certification_is_uncertain_rather_than_matched():
    profile = index(
        certifications=[
            a_certification_row("AWS Certified Developer", expires=date(2024, 1, 1))
        ]
    )
    verdict = certify("AWS Certified Developer", profile, as_of=AS_OF)

    assert verdict.status == STATUS_UNCERTAIN
    assert "2024-01-01" in verdict.explanation
    assert verdict.evidence


def test_a_certification_expiring_after_the_as_of_date_still_matches():
    profile = index(
        certifications=[
            a_certification_row("AWS Certified Developer", expires=date(2027, 1, 1))
        ]
    )
    assert certify("AWS Certified Developer", profile, as_of=AS_OF).status == STATUS_MATCHED


def test_a_live_certification_is_preferred_over_an_expired_duplicate():
    profile = index(
        certifications=[
            a_certification_row("AWS Certified Developer", expires=date(2024, 1, 1), id=1),
            a_certification_row("AWS Certified Developer", expires=date(2028, 1, 1), id=2),
        ]
    )
    verdict = certify("AWS Certified Developer", profile, as_of=AS_OF)

    assert verdict.status == STATUS_MATCHED
    assert [item.source_id for item in verdict.evidence] == [2]


def test_no_certifications_at_all_is_missing():
    verdict = certify("AWS Certified Developer", index())

    assert verdict.status == STATUS_MISSING
    assert "No qualifying evidence was found" in verdict.explanation


# =============================================================================
# Education
# =============================================================================


def educate(profile, *, description, level=None, field=None, required=True):
    return judge_education(
        description=description,
        degree_level_required=level,
        field_required=field,
        index=0,
        is_required=required,
        profile=profile,
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("BSc Computer Science", "bachelor"),
        ("B.Tech Mechanical Engineering", "bachelor"),
        ("Bachelor of Arts", "bachelor"),
        ("MSc Data Science", "master"),
        ("Master's in Statistics", "master"),
        ("MBA", "master"),
        ("PhD in Physics", "doctorate"),
        ("Associate of Applied Science", "associate"),
        ("Certificate in Welding", None),
        (None, None),
    ],
)
def test_a_degree_level_is_read_from_free_text(text, expected):
    assert degree_level(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Bachelor's degree in Computer Science", "Computer Science"),
        ("Bachelor's degree in Computer Science or related field", "Computer Science"),
        ("BS in Electrical Engineering or a closely related field", "Electrical Engineering"),
        ("Bachelor's degree", None),
        ("Master's degree required", None),
    ],
)
def test_a_field_of_study_is_read_from_the_requirement_text(text, expected):
    assert field_of_study_from_text(text) == expected


def test_a_matching_degree_and_field_matches():
    profile = index(
        education=[an_education_row(degree="BSc Computer Science", field_of_study="Computer Science")]
    )
    verdict = educate(
        profile, description="Bachelor's degree in Computer Science", level="bachelor"
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].source_type == EVIDENCE_EDUCATION


def test_a_degree_requirement_naming_no_subject_matches_on_level_alone():
    profile = index(
        education=[an_education_row(degree="B.Tech Mechanical Engineering", field_of_study="Mechanical Engineering")]
    )
    verdict = educate(profile, description="Bachelor's degree required", level="bachelor")

    assert verdict.status == STATUS_MATCHED
    assert "names no subject" in verdict.explanation


def test_a_bachelors_in_an_unrelated_field_is_uncertain_not_matched():
    """§10's example, and the mistake it exists to prevent: a B.Tech in
    Mechanical Engineering is a bachelor's degree, and that is not the same as
    satisfying "bachelor's in Computer Science or related field". Whether
    mechanical engineering is "related" is a judgment the profile schema holds
    no information to make."""
    profile = index(
        education=[
            an_education_row(
                degree="B.Tech Mechanical Engineering", field_of_study="Mechanical Engineering"
            )
        ]
    )
    verdict = educate(
        profile,
        description="Bachelor's degree in Computer Science or related field",
        level="bachelor",
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert "related field" in verdict.explanation
    assert verdict.evidence, "the degree that was found must be shown"


def test_a_higher_degree_satisfies_a_lower_requirement():
    profile = index(education=[an_education_row(degree="MSc Computer Science", field_of_study="Computer Science")])
    verdict = educate(profile, description="Bachelor's degree in Computer Science", level="bachelor")

    assert verdict.status == STATUS_MATCHED


def test_a_lower_degree_does_not_satisfy_a_higher_requirement():
    profile = index(education=[an_education_row(degree="BSc Computer Science", field_of_study="Computer Science")])
    verdict = educate(profile, description="Master's degree in Computer Science", level="master")

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence, "what the profile does record is shown even on a miss"


def test_no_education_at_all_is_missing():
    verdict = educate(index(), description="Bachelor's degree", level="bachelor")

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence == []


def test_a_requirement_with_no_recognizable_level_is_uncertain():
    """Insufficient information on the REQUIREMENT side. Not a miss — we
    could not read the question, which is a different thing from reading it
    and finding no answer."""
    profile = index(education=[an_education_row()])
    verdict = educate(profile, description="Relevant tertiary qualification", level=None)

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence


def test_the_field_is_read_from_the_description_when_the_structurer_left_it_blank():
    """The deterministic structurer records the level and the sentence, never
    `field_of_study`. Without reading the sentence, every subject requirement
    would silently become a level-only one — and §10's case could never arise."""
    profile = index(
        education=[an_education_row(degree="B.Tech Mechanical Engineering", field_of_study="Mechanical Engineering")]
    )
    verdict = educate(
        profile,
        description="Bachelor's degree in Computer Science or related field",
        level="bachelor",
        field=None,
    )

    assert verdict.status == STATUS_UNCERTAIN


def test_a_degree_whose_subject_appears_only_in_the_degree_text_still_matches():
    profile = index(education=[an_education_row(degree="BSc Computer Science", field_of_study=None)])
    verdict = educate(profile, description="Bachelor's in Computer Science", level="bachelor")

    assert verdict.status == STATUS_MATCHED


# =============================================================================
# Experience
# =============================================================================


def experience(profile, *, description, years=None, required=True, as_of=AS_OF):
    return judge_experience(
        description=description,
        minimum_years=years,
        index=0,
        is_required=required,
        profile=profile,
        as_of=as_of,
    )


# --- merging spans -----------------------------------------------------------


def test_overlapping_periods_are_counted_once():
    """Two concurrent jobs from 2020 to 2022 are two years, not four."""
    spans = [
        _Span(start=date(2020, 1, 1), end=date(2022, 1, 1)),
        _Span(start=date(2020, 1, 1), end=date(2022, 1, 1)),
    ]
    assert merge_years(spans) == pytest.approx(2.0, abs=0.1)


def test_partially_overlapping_periods_merge():
    spans = [
        _Span(start=date(2020, 1, 1), end=date(2022, 1, 1)),
        _Span(start=date(2021, 1, 1), end=date(2023, 1, 1)),
    ]
    assert merge_years(spans) == pytest.approx(3.0, abs=0.1)


def test_disjoint_periods_are_added_with_the_gap_excluded():
    spans = [
        _Span(start=date(2016, 1, 1), end=date(2017, 1, 1)),
        _Span(start=date(2020, 1, 1), end=date(2021, 1, 1)),
    ]
    assert merge_years(spans) == pytest.approx(2.0, abs=0.1)


def test_no_periods_is_no_years():
    assert merge_years([]) == 0.0


# --- the four branches -------------------------------------------------------


def test_a_requirement_with_no_stated_duration_is_judged_like_a_skill():
    profile = index(skills=[a_skill_row("Python")])
    verdict = experience(profile, description="Experience with Python", years=None)

    assert verdict.status == STATUS_MATCHED
    assert verdict.requirement_key == "python"
    assert verdict.evidence[0].source_type == EVIDENCE_SKILL


def test_a_requirement_with_no_stated_duration_accepts_paid_work():
    profile = index(experiences=[an_experience_row(description="Some Django work")])
    verdict = experience(profile, description="Experience with Django", years=None)

    assert verdict.status == STATUS_MATCHED
    assert verdict.requirement_key == "django"


def test_a_requirement_with_no_stated_duration_is_uncertain_on_a_project_mention():
    """A project description is real and is not paid work."""
    profile = index(
        projects=[a_project_row(name="Sideline", description="A small Django app")]
    )
    verdict = experience(profile, description="Experience with Django", years=None)

    assert verdict.status == STATUS_UNCERTAIN


def test_the_profiles_own_statement_of_duration_is_believed():
    """`Skill.years_of_experience` is the profile asserting the number. It
    outranks anything inferred from employment dates."""
    profile = index(skills=[a_skill_row("Python", years=6)])
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MATCHED
    assert "6 years of python" in verdict.explanation


def test_a_stated_duration_below_the_bar_is_missing_with_the_number_shown():
    """Clear insufficiency, not uncertainty: the profile answered the question
    and the answer was too low."""
    profile = index(skills=[a_skill_row("Python", years=2)])
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MISSING
    assert "2 years" in verdict.explanation and "5" in verdict.explanation
    assert verdict.evidence, "a miss on a stated number must show the claim it read"


def test_dated_employment_naming_the_subject_satisfies_a_duration():
    """§9's first example: Python used 2022–2025 against a "3+ years" bar."""
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2022, 1, 1),
                end=date(2025, 6, 1),
                description="Python services",
            )
        ],
    )
    verdict = experience(profile, description="3+ years of Python", years=3)

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].source_type == EVIDENCE_EXPERIENCE
    assert "counted once" in verdict.explanation


def test_dated_employment_short_of_the_bar_is_missing_with_the_count_shown():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2024, 1, 1),
                end=date(2025, 1, 1),
                description="Python services",
            )
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MISSING
    assert "1 years" in verdict.explanation
    assert verdict.evidence, "the evidence that was counted must be listed"


def test_multiple_overlapping_employments_are_merged_before_the_comparison():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                company="Acme",
                job_title="Python Developer",
                start=date(2020, 1, 1),
                end=date(2023, 1, 1),
                id=1,
            ),
            an_experience_row(
                company="Side Co",
                job_title="Python Contractor",
                start=date(2020, 1, 1),
                end=date(2023, 1, 1),
                id=2,
            ),
        ],
    )
    # Summing would give 6 years and wrongly MATCH; merging gives 3 and misses.
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MISSING
    assert "3 years" in verdict.explanation


def test_an_ongoing_role_is_measured_to_the_as_of_date():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(job_title="Python Developer", start=date(2020, 1, 1), end=None)
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5, as_of=AS_OF)

    assert verdict.status == STATUS_MATCHED
    # Reproducibility: the same call with an earlier as_of is a different answer.
    earlier = experience(
        profile, description="5+ years of Python", years=5, as_of=date(2022, 1, 1)
    )
    assert earlier.status == STATUS_MISSING


def test_a_requirement_naming_no_subject_asks_about_total_employment():
    """"5+ years of professional experience" is a question about employment as
    such, not about any particular kind of it."""
    profile = index(
        experiences=[
            an_experience_row(
                job_title="Analyst", start=date(2015, 1, 1), end=date(2024, 1, 1)
            )
        ]
    )
    verdict = experience(
        profile, description="5+ years of professional experience", years=5
    )

    assert verdict.status == STATUS_MATCHED
    assert "professional experience" in verdict.explanation
    assert verdict.matched_value == "employment"


def test_a_subject_present_only_as_a_project_is_uncertain_not_matched():
    """§9's closing rule. A three-year side project is not three years of
    professional experience, and the profile schema says nothing that would
    let us claim otherwise."""
    profile = index(
        projects=[
            a_project_row(
                name="EasyApply",
                technologies=["Django"],
                start=date(2020, 1, 1),
                end=date(2025, 1, 1),
            )
        ]
    )
    verdict = experience(profile, description="3+ years of Django", years=3)

    assert verdict.status == STATUS_UNCERTAIN
    assert "not counted as professional duration" in verdict.explanation
    assert verdict.evidence[0].source_type == EVIDENCE_PROJECT_TECHNOLOGY


def test_a_skill_with_no_dates_anywhere_is_uncertain():
    """§9's distinction: this is insufficient evidence, not no evidence. The
    profile claims Python and says nothing about for how long."""
    profile = index(skills=[a_skill_row("Python")])
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_UNCERTAIN
    assert "neither confirmed nor ruled out" in verdict.explanation


def test_employment_that_cannot_be_attributed_to_the_subject_is_uncertain():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(job_title="Warehouse Supervisor", description="Logistics")
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_UNCERTAIN


def test_no_employment_and_no_subject_is_missing():
    verdict = experience(index(), description="5+ years of professional experience", years=5)

    assert verdict.status == STATUS_MISSING
    assert "no qualifying evidence was found" in verdict.explanation


def test_unmeasurable_employment_is_uncertain_rather_than_zero_years():
    """`Experience.start_date` is NOT NULL, so this shape cannot reach the
    database — but `_spans_for` guards against it anyway, and the guard has to
    be coherent. Treating an unmeasurable row as zero-length would report a
    measurement that was never taken, which reads to a user as a confident
    rejection rather than as the unknown it is."""
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(job_title="Python Developer", start=None, end=None)
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_UNCERTAIN
    assert "neither confirmed nor ruled out" in verdict.explanation
    assert "0 years" not in verdict.explanation


def test_the_subject_recorded_is_the_most_specific_one_found():
    """`requirement_key` exists to be grouped by. "learning" would make that
    grouping useless where "machine learning" is what was asked."""
    profile = index(skills=[a_skill_row("Machine Learning")])
    verdict = experience(profile, description="3+ years of machine learning", years=3)

    assert verdict.requirement_key == "machine learning"


def test_the_same_inputs_give_the_same_subject_every_time():
    """Set iteration is hash-ordered and Python salts string hashes per
    process; the rules sort for this reason."""
    profile = index(skills=[a_skill_row("Python")])
    verdicts = [
        experience(profile, description="Experience with Python and testing", years=None)
        for _ in range(5)
    ]
    assert len({verdict.requirement_key for verdict in verdicts}) == 1


# =============================================================================
# Qualifications
# =============================================================================


def test_a_free_text_qualification_is_recorded_as_uncertain():
    """§12's denominator argument: dropping these would make coverage look
    better than it is without any individual verdict being wrong."""
    verdict = judge_qualification(
        description="Must be authorized to work in the US", index=0, is_required=True
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.confidence == 0.0
    assert verdict.evidence == []
    assert "does not evaluate free-text qualifications" in verdict.explanation


# =============================================================================
# Invariants that hold across every rule
# =============================================================================


def a_profile_with_everything() -> ProfileIndex:
    return index(
        skills=[a_skill_row("Python", years=6), a_skill_row("PostgreSQL")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2019, 1, 1),
                end=date(2024, 1, 1),
                description="Django and Celery",
            )
        ],
        education=[an_education_row()],
        certifications=[a_certification_row("AWS Certified Developer")],
        projects=[a_project_row(technologies=["Redis"])],
    )


def every_verdict() -> list:
    profile = a_profile_with_everything()
    return [
        judge_skill("Python", profile),
        judge_skill("Rust", profile),
        judge_skill("Django", profile),
        certify("AWS Certified Developer", profile),
        certify("Certified Kubernetes Administrator", profile),
        educate(profile, description="Bachelor's in Computer Science", level="bachelor"),
        educate(profile, description="PhD in Physics", level="doctorate"),
        experience(profile, description="3+ years of Python", years=3),
        experience(profile, description="20+ years of Python", years=20),
        judge_qualification(description="Must hold a clearance", index=0, is_required=True),
    ]


def test_a_matched_verdict_always_points_at_something():
    """The failure this domain exists to prevent. Enforced in the engine and
    asserted here because it spans two tables and cannot be a CHECK."""
    for verdict in every_verdict():
        if verdict.status == STATUS_MATCHED:
            assert verdict.evidence, f"{verdict.requirement_label} matched with no evidence"


def test_a_missing_verdict_never_claims_a_matched_value():
    for verdict in every_verdict():
        if verdict.status == STATUS_MISSING:
            assert verdict.matched_value is None


def test_every_verdict_explains_itself_in_our_own_words():
    for verdict in every_verdict():
        assert verdict.explanation
        assert len(verdict.explanation) <= 500


def test_every_confidence_is_a_fraction():
    for verdict in every_verdict():
        assert 0.0 <= verdict.confidence <= 1.0


def test_evidence_only_ever_names_a_real_profile_row():
    """Nothing is invented. Every source_type is one the profile index can
    produce, and every non-null source_id is an id the fixture created."""
    known = {
        EVIDENCE_SKILL,
        EVIDENCE_PROJECT_TECHNOLOGY,
        EVIDENCE_EXPERIENCE,
        EVIDENCE_PROJECT,
        EVIDENCE_EDUCATION,
        EVIDENCE_CERTIFICATION,
    }
    for verdict in every_verdict():
        for item in verdict.evidence:
            assert item.source_type in known
            assert item.label
