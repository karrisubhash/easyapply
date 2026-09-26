"""The work-action inflection table: what it widens, and what it must not touch.

One requirement in the real-data benchmark was answered wrongly for a purely
grammatical reason — the posting asked for "experience leading and mentoring a
team" and the profile said "Lead four engineers". `canonical()` compares
surface forms, so the verdict came back MISSING for a candidate who plainly
does the thing.

The fix is deliberately not a stemmer. Half of this file exists to prove that
the mechanism is narrow: that it cannot reach technology matching, cannot
reach the alias table, and changes nothing for any word not written down in
`morphology.py`. A morphology change that quietly widened skill matching would
be far worse than the bug it fixed, because the damage would be invisible —
a technology that stopped being recognised, or two that started being
confused, produces verdicts nobody traces back to a suffix rule.
"""

from datetime import date

import pytest

from app.models.match import CATEGORY_SKILL, STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.matching import morphology
from app.services.matching.evidence import TIER_EMPLOYMENT_DESCRIBED
from app.services.matching.morphology import (
    ACTION_LEMMAS,
    KNOWN_FORMS,
    LEMMA_TARGETS,
    action_lemma,
    same_action,
)
from app.services.matching.normalization import ALIASES, canonical
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.rules import judge_experience, judge_token_requirement
from app.services.matching.vocabulary import TECHNOLOGIES
from tests.matching_fixtures import AS_OF, a_project_row, a_skill_row, a_version, an_experience_row


def index(**kwargs) -> ProfileIndex:
    return ProfileIndex(a_version(**kwargs))


def experience(profile, *, description, years=None, as_of=AS_OF):
    return judge_experience(
        description=description,
        minimum_years=years,
        index=0,
        is_required=True,
        profile=profile,
        as_of=as_of,
    )


def skill(name, profile, *, quotes=()):
    return judge_token_requirement(
        name=name,
        category=CATEGORY_SKILL,
        index=0,
        is_required=True,
        profile=profile,
        quotes=quotes,
    )


#: The role from the benchmark case, word for word.
def a_lead_profile() -> ProfileIndex:
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
            )
        ],
    )


# =============================================================================
# The regression itself
# =============================================================================


def test_the_c10_regression_is_fixed():
    """c10 / r_ml_lead. "Experience leading and mentoring a team of engineers"
    against a role that says "Lead four engineers". matching_v1 said
    UNCERTAIN, matching_v2 said MISSING, and a person says MATCHED."""
    verdict = experience(
        a_lead_profile(), description="Experience leading and mentoring a team of engineers"
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.status != STATUS_MISSING
    assert verdict.requirement_key == "lead"
    assert verdict.evidence
    assert verdict.evidence[0].tier == TIER_EMPLOYMENT_DESCRIBED
    assert "Lead four engineers" in verdict.evidence[0].quote


def test_the_explanation_reads_as_an_answer_to_what_was_asked():
    """A verdict a person cannot read is not an explainable verdict. The
    requirement said "leading"; the explanation must not say "using lead"."""
    verdict = experience(
        a_lead_profile(), description="Experience leading and mentoring a team of engineers"
    )

    assert "involving leading" in verdict.explanation
    assert "using leading" not in verdict.explanation


# =============================================================================
# What the table supports, stated explicitly
# =============================================================================


@pytest.mark.parametrize(
    "requirement_word, profile_word",
    [
        ("leading", "Lead"),
        ("leading", "Led"),
        ("led", "Leading"),
        ("leads", "Lead"),
        ("managing", "Managed"),
        ("managed", "Managing"),
        ("management", "Manage"),
        ("mentoring", "Mentored"),
        ("developing", "Developed"),
        ("designing", "Designed"),
        ("maintaining", "Maintained"),
        ("owning", "Owned"),
        ("supervising", "Supervised"),
        ("coaching", "Coached"),
        ("delivering", "Delivered"),
        ("shipping", "Shipped"),
    ],
)
def test_supported_inflections_find_each_other(requirement_word, profile_word):
    profile = index(
        experiences=[
            an_experience_row(description=f"{profile_word} a platform team of six.")
        ]
    )
    verdict = experience(profile, description=f"Experience {requirement_word} a team")

    assert verdict.status == STATUS_MATCHED, f"{requirement_word} vs {profile_word}"


def test_lead_and_led_are_the_same_concept():
    """Asked for explicitly: "Led four engineers" is the single most common
    way a CV states this, and the past tense is unambiguous — nothing else
    lemmatizes to `lead` that way."""
    assert same_action("led", "lead")
    assert same_action("led", "leading")
    assert action_lemma("led") == "lead"


@pytest.mark.parametrize("pair", [("manage", "managing"), ("develop", "developing")])
def test_the_pairs_the_milestone_named(pair):
    assert same_action(*pair)


# =============================================================================
# What it must NOT do
# =============================================================================


@pytest.mark.parametrize(
    "left, right",
    [
        ("lead", "leverage"),
        ("lead", "learning"),
        ("manage", "manager"),
        ("design", "designate"),
        ("own", "owner"),
        ("build", "builder"),
        ("ship", "shipment"),
        ("team", "teaming"),
        ("report", "reporter"),
        ("deliver", "deliverable"),
    ],
)
def test_unrelated_or_unlisted_words_are_not_the_same_concept(left, right):
    """The table is a list, not a rule. A word that is not written down gets
    nothing, which is what keeps "manager" from becoming "manage" and
    "learning" from becoming "lead" by some plausible-looking suffix logic."""
    assert not same_action(left, right)


def test_a_word_the_table_has_never_heard_of_is_left_alone():
    for word in ("kubernetes", "django", "postgres", "fintech", "healthcare", "zigflow"):
        assert action_lemma(word) is None


def test_multi_word_phrases_are_never_inflected_as_a_unit():
    """"machine learning" must stay "machine learning"."""
    assert action_lemma("machine learning") is None
    assert action_lemma("amazon web services") is None


# =============================================================================
# The boundary: technology matching cannot reach this
# =============================================================================


def test_no_inflection_form_collides_with_a_technology_or_an_alias():
    """Enforced at import by `_assert_disjoint_from_technologies`, and asserted
    here so the reason is visible in the test suite too. A table entry that
    shadowed a technology name would change verdicts silently."""
    assert KNOWN_FORMS & TECHNOLOGIES == set()
    assert KNOWN_FORMS & set(ALIASES) == set()
    assert KNOWN_FORMS & set(ALIASES.values()) == set()


def test_the_import_guard_actually_fires():
    """The guard is only worth having if it would catch a bad entry.

    Proved by handing it a bad set rather than by mutating the real table —
    the first version of this test mutated module globals and leaked `redis`
    into a later assertion, which is exactly the class of accident the guard
    exists to prevent."""
    with pytest.raises(AssertionError, match="collides with the technology vocabulary"):
        morphology.assert_disjoint_from_technologies({"redising", "redis"})

    # And passes for the real table, which is the state the import left it in.
    morphology.assert_disjoint_from_technologies()


@pytest.mark.parametrize(
    "held, required",
    [
        ("JavaScript", "Java"),
        ("Java", "JavaScript"),
        ("React", "React Native"),
        ("React Native", "React"),
        ("Python", "Django"),
        ("Django", "Python"),
        ("PostgreSQL", "MySQL"),
        ("AWS", "Azure"),
    ],
)
def test_the_forbidden_equivalences_survive_morphology(held, required):
    """The pairs whose conflation would be the most expensive mistake this
    engine could make. Checked again here because morphology is a new way for
    two names to be treated as one."""
    verdict = skill(required, index(skills=[a_skill_row(held)]))

    assert verdict.status == STATUS_MISSING
    assert verdict.evidence == []


def test_a_skill_requirement_never_consults_the_inflection_table():
    """The boundary, tested where it can actually be observed.

    The profile says "Led"; the requirement says "Leading". The action index
    holds both under `lead`, so if `judge_token_requirement` reached for it
    this would match. It must not — skill and technology requirements compare
    surface forms and nothing else.
    """
    profile = index(experiences=[an_experience_row(description="Led a platform team.")])

    assert profile.action_evidence_for("leading").has_any, "the action index does hold it"
    assert skill("Leading", profile).status == STATUS_MISSING, (
        "but a skill requirement must not see it"
    )


def test_morphology_never_invents_a_technology():
    """A profile full of work-action verbs satisfies no technology."""
    profile = index(
        experiences=[
            an_experience_row(description="Leading, mentoring and managing a team.")
        ]
    )
    for name in ("Kubernetes", "Django", "Redis", "Spring"):
        assert skill(name, profile).status == STATUS_MISSING


def test_technology_names_that_look_inflected_are_untouched():
    """Forty-two vocabulary entries end in -s, -ed or -ing. A stemmer would
    turn `spring` into `spr` and `redis` into `redi`; this table turns them
    into nothing at all, because they are not in it."""
    for name in ("Spring", "Redis", "Kubernetes", "Pandas", "Express", "Rails", "Keras"):
        token = canonical(name)
        assert action_lemma(token) is None
        profile = index(skills=[a_skill_row(name)])
        assert skill(name, profile).status == STATUS_MATCHED


def test_alias_keys_that_look_inflected_still_resolve():
    """`postgres`, `k8s`, `js`, `nodejs` all end in -s. They must keep
    working exactly as before."""
    for held, required in [
        ("Postgres", "PostgreSQL"),
        ("K8s", "Kubernetes"),
        ("NodeJS", "Node.js"),
        ("JS", "JavaScript"),
    ]:
        assert skill(required, index(skills=[a_skill_row(held)])).status == STATUS_MATCHED


# =============================================================================
# The rest of the engine is unaffected
# =============================================================================


def test_the_action_index_is_empty_for_everything_it_does_not_cover():
    profile = index(
        skills=[a_skill_row("Django")],
        experiences=[an_experience_row(description="Built and maintained Django services.")],
    )
    assert profile.action_evidence_for("django").tier is None
    assert profile.action_evidence_for("kubernetes").tier is None
    # But the words it DOES cover are indexed.
    assert profile.action_evidence_for("building").tier == TIER_EMPLOYMENT_DESCRIBED
    assert profile.action_evidence_for("maintaining").tier == TIER_EMPLOYMENT_DESCRIBED


def test_surface_form_is_still_preferred_over_the_inflection_table():
    """The exact word wins when the profile has it — morphology only ever runs
    as a fallback, so it can widen a result and never narrow one."""
    profile = index(
        experiences=[an_experience_row(description="Leading a team and mentoring juniors.")]
    )
    verdict = experience(profile, description="Experience leading a team")

    assert verdict.status == STATUS_MATCHED
    assert verdict.requirement_key == "leading", "exact match keeps the requirement's own word"


def test_project_only_action_evidence_is_still_not_paid_work():
    """The evidence tiers apply to the action index exactly as they do
    elsewhere: a project is not employment."""
    profile = index(
        projects=[
            a_project_row(name="Sideline", description="Led a volunteer team of four.")
        ]
    )
    verdict = experience(profile, description="Experience leading a team")

    assert verdict.status == STATUS_UNCERTAIN


def test_an_evaluated_and_abandoned_action_is_still_a_mention():
    profile = index(
        experiences=[
            an_experience_row(
                description="Considered leading the platform team, but the role was cancelled."
            )
        ]
    )
    verdict = experience(profile, description="Experience leading a team")

    assert verdict.status == STATUS_UNCERTAIN


def test_experience_duration_rules_are_unchanged_by_morphology():
    """Duration requirements resolve a technology subject and never reach the
    action index, so the whole path is untouched."""
    profile = index(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Python Developer",
                start=date(2019, 6, 1),
                end=None,
                description="Building Python services.",
            )
        ],
    )
    verdict = experience(profile, description="5+ years of Python", years=5)

    assert verdict.status == STATUS_MATCHED
    assert verdict.requirement_key == "python"


def test_the_table_is_deterministic_across_lookups():
    """Plain dict lookups, sorted iteration upstream — no hash-order
    dependence anywhere in the path."""
    for _ in range(20):
        assert action_lemma("leading") == "lead"
    assert sorted(LEMMA_TARGETS) == sorted(set(ACTION_LEMMAS.values()))


def test_every_table_entry_is_lowercase_and_canonical():
    """The table is consulted with tokens `canonical()` produced, so an entry
    that is not itself in canonical form could never fire."""
    for key, value in ACTION_LEMMAS.items():
        assert key == canonical(key), key
        assert value == canonical(value), value
        assert key != value, f"{key} maps to itself"
