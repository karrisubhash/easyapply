"""Step 3 of Matching V5: `negation.negation_fires`, kept apart from
`vocabulary.NON_DOING_MARKERS`.

Every positive case here is one of the ten `explicit_non_use` non_doing_v1
cases or `pl-029`'s false-`MATCHED` cascade clause -- texts that name no
NON_DOING_MARKERS word at all. Every control is a sentence built specifically
to probe the reason a bare negator is unsafe to add to a flat word list: a
negation particle attaches to affirmative predicates constantly in ordinary
resume prose, and the patterns must not fire on any of them.
"""

import pytest

from app.services.matching.negation import EXPLICIT_NEGATION_PATTERNS, negation_fires
from app.services.matching.vocabulary import is_non_doing


# =============================================================================
# Positive: the ten explicit_non_use cases, plus pl-029's negation clause
# =============================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Never used Kubernetes outside of a single tutorial.",
        "Has not worked with Rust in a professional capacity.",
        "No hands-on GraphQL experience; only read about it in blog posts.",
        "Does not have production experience with Elasticsearch.",
        "Read about Svelte once but never wrote a line of it.",
        "Kubernetes is on the wishlist for next year; nothing has been done with it so far.",
        "Would like to work with Terraform someday but has no experience with it yet.",
        "Unable to find time to try Rust despite several attempts to start.",
        "Claims no direct experience with Scala beyond a university course.",
        "Zero production hours with Rust to date.",
        "no Rust code has shipped yet.",
    ],
)
def test_explicit_negation_fires_on_every_non_doing_v1_case(text):
    assert negation_fires(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Never used Kubernetes outside of a single tutorial.",
        "Has not worked with Rust in a professional capacity.",
        "No hands-on GraphQL experience; only read about it in blog posts.",
        "Does not have production experience with Elasticsearch.",
        "Read about Svelte once but never wrote a line of it.",
        "Kubernetes is on the wishlist for next year; nothing has been done with it so far.",
        "Would like to work with Terraform someday but has no experience with it yet.",
        "Unable to find time to try Rust despite several attempts to start.",
        "Claims no direct experience with Scala beyond a university course.",
        "Zero production hours with Rust to date.",
    ],
)
def test_none_of_these_texts_contain_a_non_doing_marker_at_all(text):
    """The whole reason this module exists: is_non_doing() alone cannot see
    these. If this test ever fails, one of these sentences accidentally
    picked up a real marker word and the negation-only claim is false."""
    assert is_non_doing(text) is False


# =============================================================================
# Negative: bare negators in ordinary, affirmative resume prose must not fire
# =============================================================================


@pytest.mark.parametrize(
    "text",
    [
        "This is not a hobby project, it has been in production for six years.",
        "Kubernetes in production, not just in a course.",
        "Kept the service running with no downtime for three years.",
        "Worked with no direct supervision on the Kafka migration for six years.",
        "This was not a small project; it shipped to every region.",
        "The team never stopped shipping new Django features for a decade.",
        # one adversarial control per pattern, close to a real near-miss
        "Reduced experience onboarding time with no changes to the interview loop.",
        "There is no reason it hasn't shipped already; the delay was scheduling.",
        "Never one to leave things unfinished, she shipped every sprint on time.",
        "Not only did it ship, it shipped ahead of schedule.",
        "Zero in on the root cause before touching production code.",
        "Unable to reproduce the bug, the team shipped a monitoring fix instead.",
    ],
)
def test_negation_patterns_do_not_fire_on_affirmative_prose(text):
    assert negation_fires(text) is False


def test_negation_fires_handles_none_and_empty_input():
    assert negation_fires("") is False
    assert negation_fires(None) is False  # type: ignore[arg-type]


def test_there_are_exactly_the_curated_patterns_no_more_no_less():
    """Pinned so a future change to this list is a visible, deliberate diff,
    not an accidental regex edit -- the same discipline
    vocabulary.NON_DOING_MARKERS is held to. Six from matching_v5; four added
    in matching_v5_1, each for a denial an offline probe showed being read as
    paid work (see the tests below)."""
    assert len(EXPLICIT_NEGATION_PATTERNS) == 10


# =============================================================================
# matching_v5_1: denials the six v5 patterns read as affirmative
# =============================================================================
#
# Every sentence here, as the second clause of a paid role's description, was
# graded `employment_described` under matching_v5 and counted toward "3+ years
# of Django" — the role's whole duration, cited as "Demonstrated in paid work".


@pytest.mark.parametrize(
    "text",
    [
        "Did not use Django.",
        "Didn't use Django.",
        "Doesn’t use Kafka.",
        "Haven't used Django.",
        "Has not touched Terraform.",
        "Did not work with Django.",
        "Unfamiliar with Django.",
        "I'm unfamiliar with Go.",
        "Lacks Django experience.",
        "Lacking production exposure to Go.",
        "Django was never adopted.",
        "Kubernetes was not adopted by the team.",
        "Never had the chance to use Django.",
        "Never had an opportunity to work with Rust.",
    ],
)
def test_v5_1_recognises_auxiliary_and_span_initial_denials(text):
    assert negation_fires(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # "unfamiliar with" / "lack" about OTHER people, mid-sentence
        "Onboarded new hires unfamiliar with the codebase onto the Django services.",
        "Addressed the team's lack of test experience by introducing pytest.",
        # auxiliary negation of a predicate that is not use
        "Didn't try to hide the complexity; documented the Kafka design.",
        "Did not stop at Django; also built the Celery pipeline.",
        "Did not work until the cache was warmed, then served all Django traffic.",
        # adoption, affirmed
        "Adopted Kubernetes across all services.",
        "Had never been adopted before I introduced Terraform to the team.",
    ],
)
def test_v5_1_patterns_do_not_fire_on_their_near_misses(text):
    assert negation_fires(text) is False


def test_a_denial_in_a_paid_role_is_no_longer_counted_as_paid_work():
    """The end-to-end shape of the defect, through the index and the rules."""
    from datetime import date
    from types import SimpleNamespace as N

    from app.services.matching import rules
    from app.services.matching.profile_index import ProfileIndex

    version = N(
        id=1,
        experiences=[
            N(
                id=1,
                job_title="Backend Engineer",
                company="Acme",
                description="Built billing services in Flask. Unfamiliar with Django.",
                start_date=date(2020, 1, 1),
                end_date=None,
                location=None,
            )
        ],
        skills=[],
        projects=[],
        education=[],
        certifications=[],
    )
    judgment = rules.judge_experience(
        description="3+ years of Django",
        minimum_years=3,
        index=0,
        is_required=True,
        profile=ProfileIndex(version),
        as_of=date(2026, 9, 1),
    )
    assert judgment.status != "matched"
    assert all(item.tier != "employment_described" for item in judgment.evidence)
