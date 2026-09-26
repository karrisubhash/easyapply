"""The two defects the real-data benchmark found, pinned so they cannot return.

Every test here is a specific failure that matching_v1 produced against real
profiles and real postings, written as the case that produced it. They are
separate from `test_matching_rules.py` on purpose: that file covers the rules
as specified, and this one covers the places where the specification itself
turned out to be wrong.

The two defects:

**Subject resolution by keyword.** "3+ years building production services with
Django" resolved `building` as its subject, so a profile whose unrelated role
said "building credit-risk models" was credited with 7.7 years of Django
experience it did not have. That is the worst output this engine can produce —
a confident MATCHED nobody has a reason to check.

**Evidence sorted into two bins.** Six years of daily React described in a
current paid role scored the same as a word appearing in passing, while a
graduate's uncorroborated skills-list entry scored MATCHED at 0.95 against a
posting that said "in production, not just in a course".
"""

from datetime import date

import pytest

from app.models.match import CATEGORY_SKILL, STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.matching.evidence import (
    STANDARD_ANY_EVIDENCE,
    STANDARD_PROFESSIONAL_USE,
    TIER_DECLARED_CORROBORATED,
    TIER_DECLARED_ONLY,
    TIER_EMPLOYMENT_DESCRIBED,
    TIER_MENTIONED,
    standard_for,
)
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.rules import (
    asks_about_total_employment,
    judge_experience,
    judge_token_requirement,
    resolve_technology_subject,
)
from app.services.matching.vocabulary import is_generic, is_technology
from tests.matching_fixtures import (
    AS_OF,
    a_project_row,
    a_skill_row,
    a_version,
    an_experience_row,
)


def index(**kwargs) -> ProfileIndex:
    return ProfileIndex(a_version(**kwargs))


def skill(name, profile, *, quotes=(), required=True):
    return judge_token_requirement(
        name=name,
        category=CATEGORY_SKILL,
        index=0,
        is_required=required,
        profile=profile,
        quotes=quotes,
    )


def experience(profile, *, description, years=None, quotes=(), as_of=AS_OF):
    return judge_experience(
        description=description,
        minimum_years=years,
        index=0,
        is_required=True,
        profile=profile,
        as_of=as_of,
        quotes=quotes,
    )


#: The profile from the benchmark case that produced the false match: eleven
#: years of machine-learning work, not one line of Django.
def a_machine_learning_profile() -> ProfileIndex:
    return index(
        skills=[a_skill_row("Python", years=11), a_skill_row("PyTorch", years=5)],
        experiences=[
            an_experience_row(
                company="Quantel Capital",
                job_title="Senior Machine Learning Engineer",
                start=date(2019, 1, 1),
                end=None,
                description=(
                    "Lead four engineers building credit-risk models for a regulated "
                    "consumer lender. PyTorch and Spark on a shared feature store."
                ),
            ),
            an_experience_row(
                company="Orbital Analytics",
                job_title="Data Scientist",
                start=date(2015, 3, 1),
                end=date(2018, 12, 31),
                description="Demand forecasting models for logistics clients, in Python.",
            ),
        ],
    )


# =============================================================================
# 1-3. Subject resolution
# =============================================================================


def test_the_benchmark_false_match_cannot_happen_again():
    """THE regression. A profile with no Django, against a Django duration
    requirement whose wording contains a word that appears in an unrelated
    role, must not come back MATCHED."""
    verdict = experience(
        a_machine_learning_profile(),
        description="3+ years building production services with Django",
        years=3,
    )

    assert verdict.status == STATUS_MISSING
    assert verdict.requirement_key == "django"
    assert verdict.evidence == []
    assert "django" in verdict.explanation


@pytest.mark.parametrize(
    "word",
    ["building", "developing", "working", "designing", "creating", "services",
     "production", "software", "systems", "platform", "engineering"],
)
def test_a_generic_work_word_is_never_a_technology_subject(word):
    """Not by a stopword list that someone has to remember to extend, but
    because these words describe the act of the work rather than a thing
    worked with."""
    assert is_generic(word)
    assert not is_technology(word)


def test_a_generic_word_the_profile_declares_is_still_not_a_subject():
    """A profile that lists a skill called "Platform" does not make `platform`
    a technology subject for everyone else's requirements."""
    profile = index(skills=[a_skill_row("Platform"), a_skill_row("Django")])

    assert resolve_technology_subject(
        "3+ years building production services with Django", profile
    ) == "django"


def test_the_subject_of_a_requirement_is_the_technology_it_names():
    profile = index(skills=[a_skill_row("Django")])
    for description, expected in [
        ("3+ years building production services with Django", "django"),
        ("5+ years of hands-on Kubernetes", "kubernetes"),
        ("4+ years developing React applications", "react"),
        ("Minimum 5 years of professional PostgreSQL work", "postgresql"),
    ]:
        assert resolve_technology_subject(description, profile) == expected


def test_a_technology_the_vocabulary_has_never_heard_of_resolves_if_declared():
    """The curated list does not have to be exhaustive: a profile that calls
    something a skill makes it resolvable for that match."""
    unknown = index(skills=[a_skill_row("Zigflow")])

    assert resolve_technology_subject("3+ years of Zigflow", unknown) == "zigflow"
    assert resolve_technology_subject("3+ years of Zigflow", index()) is None


def test_django_experience_is_attributed_only_to_roles_that_name_django():
    """The other half of the same defect: a duration must be measured from
    the roles that actually demonstrate the technology, not from every role
    that shares a word with the requirement."""
    profile = index(
        skills=[a_skill_row("Django")],
        experiences=[
            an_experience_row(
                company="Acme",
                job_title="Backend Engineer",
                start=date(2021, 3, 1),
                end=None,
                description="Built and maintained Django REST services.",
                id=1,
            ),
            an_experience_row(
                company="Older Co",
                job_title="Analyst",
                start=date(2010, 1, 1),
                end=date(2020, 1, 1),
                description="Building spreadsheets and production reports.",
                id=2,
            ),
        ],
    )
    verdict = experience(
        profile, description="3+ years building production services with Django", years=3
    )

    assert verdict.status == STATUS_MATCHED
    # Only the Django role counted: ten years at "Older Co" are not Django years.
    assert [item.source_id for item in verdict.evidence] == [1]
    # 2021-03 to the as-of date, and not a day of the ten years at Older Co.
    assert "5.6 years" in verdict.explanation


def test_a_requirement_naming_only_a_kind_of_work_asks_about_total_employment():
    assert asks_about_total_employment("5+ years of professional experience")
    assert asks_about_total_employment("5+ years of professional software engineering experience")
    assert not asks_about_total_employment("3+ years with Django")
    # Neither a technology nor a kind of work: not answerable, not invented.
    assert not asks_about_total_employment("5+ years of fintech experience")


def test_an_unresolvable_duration_requirement_is_uncertain_rather_than_guessed():
    profile = index(
        experiences=[an_experience_row(start=date(2010, 1, 1), end=None)]
    )
    verdict = experience(profile, description="5+ years of fintech experience", years=5)

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.requirement_key is None
    assert "could not be checked" in verdict.explanation


def test_total_employment_is_treated_as_an_upper_bound():
    """Short of the bar is a definite no whatever the field — you cannot have
    six years of anything inside seven months of employment. Clearing the bar
    only answers the question if the profile shows the work was that kind."""
    thin = index(
        experiences=[
            an_experience_row(
                job_title="Software Engineering Intern",
                start=date(2025, 6, 1),
                end=date(2025, 12, 31),
            )
        ]
    )
    assert experience(
        thin, description="6+ years of professional infrastructure experience", years=6
    ).status == STATUS_MISSING

    unrelated = index(
        experiences=[
            an_experience_row(
                job_title="Mechanical Design Engineer",
                start=date(2017, 9, 1),
                end=date(2023, 11, 30),
                description="Mechanical design for industrial pump assemblies.",
            )
        ]
    )
    verdict = experience(
        unrelated, description="5+ years of professional software experience", years=5
    )
    assert verdict.status == STATUS_UNCERTAIN
    assert "identifies itself with" in verdict.explanation


def test_overlapping_employment_is_still_merged_not_summed():
    """Preserved from matching_v1, and easy to lose in a rewrite."""
    profile = index(
        skills=[a_skill_row("Go")],
        experiences=[
            an_experience_row(
                company="Meridian",
                job_title="Platform Contractor",
                start=date(2018, 1, 1),
                end=date(2021, 6, 30),
                description="Go services for a bank.",
                id=1,
            ),
            an_experience_row(
                company="Northstar",
                job_title="DevOps Contractor",
                start=date(2020, 3, 1),
                end=date(2022, 12, 31),
                description="Go services and Helm charts.",
                id=2,
            ),
        ],
    )
    verdict = experience(profile, description="6+ years of Go", years=6)

    # Summed: 3.5 + 2.8 = 6.3, which would clear the bar. Merged: 5.0, which
    # does not. The merge is what makes the answer honest.
    assert verdict.status == STATUS_MISSING
    assert "5 years" in verdict.explanation


def test_subject_resolution_is_stable_across_runs():
    """Set iteration is hash-ordered and Python salts string hashes per
    process; the resolution sorts for exactly this reason."""
    profile = index(skills=[a_skill_row("Python"), a_skill_row("Django")])
    resolved = {
        resolve_technology_subject("3+ years of Python and Django services", profile)
        for _ in range(20)
    }
    assert len(resolved) == 1


# =============================================================================
# 4-7. Evidence tiers
# =============================================================================


def a_graduate() -> ProfileIndex:
    """The benchmark's skill-stuffing case: seven declared skills, one
    internship, and nothing corroborating four of them."""
    return index(
        skills=[
            a_skill_row("Python"),
            a_skill_row("Kubernetes"),
            a_skill_row("Terraform"),
        ],
        experiences=[
            an_experience_row(
                company="Tallow & Co",
                job_title="Software Engineering Intern",
                start=date(2025, 6, 1),
                end=date(2025, 12, 31),
                description="Wrote Python scripts for internal reporting.",
            )
        ],
    )


def test_a_declared_only_skill_answers_a_requirement_that_asks_only_for_the_skill():
    verdict = skill("Kubernetes", a_graduate(), quotes=("Nice to have: Kubernetes",))

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence_standard == STANDARD_ANY_EVIDENCE
    assert verdict.evidence[0].tier == TIER_DECLARED_ONLY
    # And is believed less firmly than corroborated evidence would be.
    assert verdict.confidence == pytest.approx(0.75)


def test_a_declared_only_skill_does_not_answer_a_production_requirement():
    """The benchmark's false match, in its own words: the posting said
    "in production, not just in a course" and the profile is a skills list."""
    verdict = skill(
        "Kubernetes",
        a_graduate(),
        quotes=("Kubernetes in production, not just in a course",),
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence_standard == STANDARD_PROFESSIONAL_USE
    assert verdict.evidence[0].tier == TIER_DECLARED_ONLY
    # The evidence is kept, not discarded: an UNCERTAIN has to show what it
    # found and say why it was not enough.
    assert verdict.evidence
    assert "no paid work using it" in verdict.explanation


def test_a_declared_only_skill_does_not_answer_a_production_duration_requirement():
    verdict = experience(
        a_graduate(),
        description="3+ years of production Kubernetes experience",
        years=3,
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.requirement_key == "kubernetes"
    assert "no dated paid role demonstrates it" in verdict.explanation


def test_paid_role_evidence_answers_a_professional_requirement():
    """Tier 3. The largest false-missing class the benchmark found: the work
    is described in a paid role and simply absent from the skills list."""
    profile = index(
        experiences=[
            an_experience_row(
                company="Pixelforge",
                job_title="Frontend Engineer",
                start=date(2020, 9, 1),
                end=None,
                description="Built and shipped the customer dashboard in React with TypeScript.",
            )
        ]
    )
    verdict = skill(
        "React", profile, quotes=("React, and 4+ years building production web front-ends",)
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence_standard == STANDARD_PROFESSIONAL_USE
    assert verdict.evidence[0].tier == TIER_EMPLOYMENT_DESCRIBED
    assert "Built and shipped" in verdict.evidence[0].quote


def test_project_only_evidence_is_not_professional_experience():
    """Preserved, and now enforced structurally: the duration rules read
    employment evidence and a project can never appear there."""
    profile = index(
        projects=[
            a_project_row(
                name="OpenLedger",
                technologies=["Django"],
                description="Bookkeeping tool maintained since 2020.",
                start=date(2020, 1, 1),
                end=None,
            )
        ]
    )
    verdict = experience(
        profile, description="4+ years of professional Django development", years=4
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert "Project work is not counted as professional duration" in verdict.explanation


def test_a_technology_evaluated_and_abandoned_is_a_mention_not_experience():
    """A clause that says the work was not done cannot demonstrate it. This is
    what stops paid-role prose from becoming a blanket promotion."""
    profile = index(
        experiences=[
            an_experience_row(
                job_title="Junior Web Developer",
                description=(
                    "jQuery and vanilla JavaScript for a booking site. Evaluated React "
                    "Native for a mobile app, but the project was cancelled before any "
                    "code shipped."
                ),
            )
        ]
    )
    verdict = skill("React Native", profile)

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence[0].tier == TIER_MENTIONED
    # matching_v5 quotes the SPAN that actually names React Native, not the
    # whole clause: "while building X" and "before Y happened" are two
    # claims, not one, and the marker here ("evaluated") is the one that
    # applies to this token. See span_split.py and profile_index.py's
    # _index_experience_description_clause.
    assert "Evaluated" in verdict.evidence[0].quote


def test_a_technology_repeated_across_a_split_stays_a_mention_not_employment():
    """matching_v5's one identified regression risk, pinned.

    Splitting a clause at a scope-reset point ("Evaluated Kafka, before
    abandoning Kafka entirely...") can put the SAME token on both sides. The
    second span has no marker of its own -- "abandoning" (the gerund) is not
    in NON_DOING_MARKERS, only "abandoned" (past tense) is -- so naively
    trusting the tier's ordinary "employment beats mentions" precedence
    would flip this to employment_described, a false MATCHED that matching_v4
    never produced (the whole, unsplit clause reads correctly, if only
    because it never looked closely enough to get it wrong). A LATER span's
    literal repeat of a token already pinned to `mentions` by an EARLIER
    span of the SAME clause must not promote it. See
    profile_index.py's `_index_experience_description_clause`.
    """
    profile = index(
        experiences=[
            an_experience_row(
                description="Evaluated Kafka, before abandoning Kafka entirely for cost reasons."
            )
        ]
    )
    verdict = skill("Kafka", profile)

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence[0].tier == TIER_MENTIONED


def test_a_technology_named_in_a_library_name_is_not_evaluated_and_abandoned():
    """The inverse of the test above, and the defect it hid.

    `is_non_doing` tokenised on `[a-z]+`, so "scikit-learn" arrived as
    `{"scikit", "learn"}` and `learn` is a marker — the clause describing four
    years of paid modelling work was graded as a disclaimer, and this
    profile's Python fell to `declared_only`.

    The fixture above this one writes the same role as "...in Python.", with
    the scikit-learn dropped, which is why no unit test ever saw it. Written
    out in full here so it cannot be dropped again.
    """
    profile = index(
        skills=[a_skill_row("Python", years=11)],
        experiences=[
            an_experience_row(
                company="Orbital Analytics",
                job_title="Data Scientist",
                start=date(2015, 3, 1),
                end=date(2018, 12, 31),
                description=(
                    "Demand forecasting models for logistics clients, in Python "
                    "and scikit-learn."
                ),
            )
        ],
    )
    verdict = skill("Python", profile)

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].tier == TIER_DECLARED_CORROBORATED
    assert "scikit-learn" in verdict.evidence[0].quote


def test_declared_and_demonstrated_is_the_strongest_tier():
    profile = index(
        skills=[a_skill_row("Django")],
        experiences=[
            an_experience_row(description="Built and maintained Django REST services.")
        ],
    )
    verdict = skill("Django", profile, quotes=("3+ years of production Django",))

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence[0].tier == TIER_DECLARED_CORROBORATED
    assert verdict.confidence == pytest.approx(0.95)


@pytest.mark.parametrize(
    "quote, expected",
    [
        ("Kubernetes", STANDARD_ANY_EVIDENCE),
        ("Nice to have: Redis", STANDARD_ANY_EVIDENCE),
        ("Any React exposure", STANDARD_ANY_EVIDENCE),
        ("Kubernetes in production, not just in a course", STANDARD_PROFESSIONAL_USE),
        ("4+ years of professional Django development", STANDARD_PROFESSIONAL_USE),
        ("Demonstrable, hands-on experience with Terraform", STANDARD_PROFESSIONAL_USE),
        ("Proven track record with Go at scale", STANDARD_PROFESSIONAL_USE),
    ],
)
def test_the_bar_is_read_from_the_postings_own_words(quote, expected):
    assert standard_for(quote) == expected


def test_an_unverified_quote_cannot_raise_the_bar():
    """A citation the server could not find in the description may have been
    fabricated by a provider. Letting it change a verdict would hand a model
    influence over the deterministic engine."""
    from app.schemas.job_candidate import JobProvenance, JobSkillItem
    from app.services.matching.engine import _quotes

    item = JobSkillItem(
        name="Kubernetes",
        provenance=[
            JobProvenance(
                source_section="requirements",
                quote="Kubernetes in production, not just in a course",
                confidence=0.9,
                verified=False,
            )
        ],
    )
    assert _quotes(item) == ()


# =============================================================================
# 8-11. Nothing that worked before may have stopped working
# =============================================================================


@pytest.mark.parametrize(
    "held, required",
    [("Python", "Python"), ("PostgreSQL", "PostgreSQL"), ("Docker", "Docker")],
)
def test_exact_technology_matches_are_unchanged(held, required):
    assert skill(required, index(skills=[a_skill_row(held)])).status == STATUS_MATCHED


@pytest.mark.parametrize(
    "held, required",
    [
        ("Postgres", "PostgreSQL"),
        ("K8s", "Kubernetes"),
        ("Golang", "Go"),
        ("Amazon Web Services", "AWS"),
        ("NodeJS", "Node.js"),
    ],
)
def test_supported_aliases_are_unchanged(held, required):
    assert skill(required, index(skills=[a_skill_row(held)])).status == STATUS_MATCHED


@pytest.mark.parametrize(
    "held, required",
    [
        ("JavaScript", "Java"),
        ("Java", "JavaScript"),
        ("React", "React Native"),
        ("React Native", "React"),
        ("PostgreSQL", "MySQL"),
        ("Django", "Python"),
        ("Python", "Django"),
    ],
)
def test_the_forbidden_equivalences_are_still_forbidden(held, required):
    """The pairs whose conflation would be the most expensive mistake this
    engine could make. Graded evidence changed how strongly a claim is
    believed; it changed nothing about WHICH claims are the same claim."""
    verdict = skill(required, index(skills=[a_skill_row(held)]))

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence == []


def test_the_forbidden_equivalences_hold_for_prose_evidence_too():
    """The new tier reads role descriptions, which is a new surface for a
    false equivalence to appear on."""
    profile = index(
        experiences=[
            an_experience_row(description="Deep JavaScript work across the stack.")
        ]
    )
    assert skill("Java", profile).status == STATUS_MISSING


def test_a_valid_duration_calculation_is_unchanged():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2019, 6, 1),
                end=None,
                description="Python services.",
            )
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5, as_of=AS_OF)

    assert verdict.status == STATUS_MATCHED
    assert "7.3 years" in verdict.explanation


def test_an_insufficient_duration_is_still_a_definite_no():
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2024, 1, 1),
                end=date(2025, 1, 1),
                description="Python services.",
            )
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence, "a miss on a measured duration must show what was counted"
