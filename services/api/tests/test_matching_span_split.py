"""Step 1 of Matching V5: `span_split.split_into_spans` and its position
finder, `find_marker_span`.

Every test here is either a specific `non_doing_v1` case this module exists
to fix, or a specific risk named while designing it
(`docs/matching-v5-implementation-design.md` §2.1, §3.1, §6) — most
importantly the case that proves *marker-relative* splitting was the right
call over unconditional comma-splitting: a bare comma must never become a
span boundary unless a marker precedes it, or "Redis, evaluated for six
months, was never adopted." would isolate Redis from the very word that
describes it.
"""

import pytest

from app.services.matching.span_split import (
    SCOPE_RESET_CONNECTORS,
    find_marker_span,
    find_next_scope_reset,
    is_reattachment_opener,
    nearest_preceding_token,
    split_into_spans,
)
from app.services.matching.vocabulary import is_non_doing, is_technology


def _relevant(token: str) -> bool:
    return is_technology(token)


# =============================================================================
# The master invariant: no marker, no split; marker with nothing after it, no split
# =============================================================================


def test_a_clause_with_no_marker_is_never_split():
    clause = "Built and maintained Django REST services for six years."
    assert split_into_spans(clause) == [clause]


def test_a_marker_with_no_scope_reset_point_after_it_is_never_split():
    clause = "Evaluated Kafka thoroughly over several sprints"
    assert split_into_spans(clause) == [clause]


def test_empty_and_blank_input_produce_no_spans():
    assert split_into_spans("") == []
    assert split_into_spans("   ") == []


# =============================================================================
# The risk that ruled out unconditional comma-splitting
# =============================================================================


def test_a_comma_before_the_marker_does_not_isolate_the_technology():
    """The exact case named in the design doc as the reason splitting is
    marker-relative rather than "every comma is a boundary": if the
    technology sits BEFORE the marker, no split may separate the two."""
    clause = "Redis, evaluated for six months, was never adopted."
    spans = split_into_spans(clause)
    assert spans[0] == "Redis, evaluated for six months,"
    assert is_non_doing(spans[0]) is True
    # Redis never ends up in a span that reads as affirmative.
    assert all(is_non_doing(span) for span in spans if "Redis" in span)


# =============================================================================
# Each connector, one non_doing_v1 case per row of the design doc's table
# =============================================================================


@pytest.mark.parametrize(
    "clause, before_marker_technology, after_marker_technology",
    [
        # pl-001: "while", no comma before it
        (
            "Deprecated the legacy PHP monolith while building its replacement "
            "in Go, which now serves all production traffic.",
            "PHP",
            "Go",
        ),
        # pl-002: "but", no comma before it
        (
            "Investigated MongoDB as a replacement for the reporting store but "
            "kept PostgreSQL, which continues to serve every nightly report.",
            "MongoDB",
            "PostgreSQL",
        ),
        # sd-090: "before", no comma anywhere in the sentence
        (
            "Rejected three vendor proposals before settling on the in-house "
            "Elasticsearch cluster that has indexed every support ticket since 2019.",
            None,
            "Elasticsearch",
        ),
        # sd-091: "and ultimately"
        (
            "Considered candidates for a new observability stack and ultimately "
            "expanded the team's existing Grafana dashboards, now viewed daily "
            "by every on-call engineer.",
            None,
            "Grafana",
        ),
        # sd-092: "and then"
        (
            "Explored candidate frameworks during onboarding week and then spent "
            "the next three years building the company's core API in Express.",
            None,
            "Express",
        ),
        # pl-009: "in favor of"
        (
            "Retired the team's homegrown job scheduler in favor of Airflow, "
            "which has orchestrated every nightly pipeline in production for "
            "the past six years.",
            None,
            "Airflow",
        ),
    ],
)
def test_a_connector_separates_the_caveat_from_the_resolution(
    clause, before_marker_technology, after_marker_technology
):
    spans = split_into_spans(clause)
    assert len(spans) == 2, spans
    assert is_non_doing(spans[0]) is True
    assert is_non_doing(spans[1]) is False
    if before_marker_technology:
        assert before_marker_technology in spans[0]
        assert before_marker_technology not in spans[1]
    assert after_marker_technology in spans[1]
    assert after_marker_technology not in spans[0]


def test_but_and_while_are_both_in_the_curated_connector_list():
    assert "but" in SCOPE_RESET_CONNECTORS
    assert "while" in SCOPE_RESET_CONNECTORS
    assert "though" in SCOPE_RESET_CONNECTORS
    assert "although" in SCOPE_RESET_CONNECTORS


def test_bare_and_or_are_never_connectors():
    """Coordinating "and"/"or" extend one claim across a list; they must
    never reset scope, or "Redis and Memcached were both evaluated and
    rejected" would isolate Redis from its own marker."""
    assert "and" not in SCOPE_RESET_CONNECTORS
    assert "or" not in SCOPE_RESET_CONNECTORS
    clause = "Redis and Memcached were both evaluated and rejected."
    assert split_into_spans(clause) == [clause]


def test_a_comma_after_a_marker_is_still_a_boundary_with_no_connector_present():
    """pl-006 and pl-003's shape: a bare comma, no connector word at all,
    but the marker precedes it."""
    clause = (
        "Migrating away from Heroku, the platform team now runs everything on "
        "AWS, which has hosted production traffic for the past five years."
    )
    spans = split_into_spans(clause)
    assert spans[0] == "Migrating away from Heroku,"
    assert is_non_doing(spans[0]) is True
    assert "AWS" not in spans[0]
    assert any("AWS" in span and is_non_doing(span) is False for span in spans[1:])


# =============================================================================
# Protected names are never split apart
# =============================================================================


@pytest.mark.parametrize(
    "clause",
    [
        "Demand forecasting models for logistics clients, in Python and scikit-learn.",
        "Machine learning pipelines in Python and Spark, shipped to every region.",
    ],
)
def test_a_marker_bearing_name_is_never_the_reason_a_split_happens(clause):
    """find_marker_span must not treat "learn" inside a protected name as a
    marker: these clauses contain no marker at all once the name is
    neutralised, so no split should be proposed."""
    assert find_marker_span(clause) is None
    assert split_into_spans(clause) == [clause]


def test_a_genuine_marker_still_splits_even_near_a_protected_name():
    """The protection must not swallow an unrelated, genuine marker
    elsewhere in the same clause."""
    clause = "Shipped a Scikit-Learn model, before the next quarter began, to production."
    spans = split_into_spans(clause)
    assert len(spans) == 1, "no marker other than the protected name is present"
    assert spans == [clause]


def test_find_marker_span_returns_none_for_a_marker_free_text():
    assert find_marker_span("built and shipped django services") is None


def test_find_marker_span_finds_a_single_word_marker():
    span = find_marker_span("evaluated kafka and dropped it")
    assert span == (0, 9)


def test_find_marker_span_finds_a_multiword_marker():
    text = "we are migrating away from angular"
    span = find_marker_span(text)
    assert text[span[0] : span[1]] == "migrating away"


# =============================================================================
# find_next_scope_reset
# =============================================================================


def test_find_next_scope_reset_prefers_whichever_comes_first():
    text = "evaluated it, but nothing shipped"
    # comma is at index 12 (points to 13); "but" starts at index 14
    assert find_next_scope_reset(text, after=0) == 14 or find_next_scope_reset(text, after=0) == 13


def test_find_next_scope_reset_returns_none_when_nothing_follows():
    assert find_next_scope_reset("evaluated it thoroughly", after=0) is None


# =============================================================================
# Determinism
# =============================================================================


def test_splitting_is_deterministic_across_repeated_calls():
    clause = (
        "Deprecated the legacy PHP monolith while building its replacement "
        "in Go, which now serves all production traffic."
    )
    results = {tuple(split_into_spans(clause)) for _ in range(20)}
    assert len(results) == 1


# =============================================================================
# Step 2: is_reattachment_opener
# =============================================================================


@pytest.mark.parametrize(
    "span",
    [
        "which have handled every internal call in production since 2017.",
        "that now serves every mobile client in production.",
        "who has run production workloads for five years.",
        ", which has orchestrated every nightly pipeline in production for six years.",
        "now streaming every order in real time.",
    ],
)
def test_relative_and_participle_openers_are_recognised(span):
    assert is_reattachment_opener(span) is True


@pytest.mark.parametrize(
    "span",
    [
        "before settling on the in-house Elasticsearch cluster.",
        "the platform team now runs everything on AWS.",
        "built and maintained Django services for six years.",
    ],
)
def test_ordinary_spans_are_not_openers(span):
    assert is_reattachment_opener(span) is False


# =============================================================================
# Step 2: nearest_preceding_token -- the position-based fix, pinned
# =============================================================================


def test_nearest_preceding_token_ignores_generic_words():
    """The bug this design caught: canonical() normalizes ANY text, so a
    naive position search hands back "services" -- the last word before the
    comma -- instead of the technology actually named. is_relevant is what
    makes the difference; without it this test fails exactly as the design
    process's first draft did."""
    span = "Prototyped a GraphQL layer on top of the existing gRPC services,"
    assert nearest_preceding_token([span], is_relevant=_relevant) == "grpc"


def test_nearest_preceding_token_picks_the_textually_later_of_two_technologies():
    """The exact shape that broke an unordered-set implementation: TWO
    technologies in the one preceding span. This must return the same
    answer every time, not whichever a Python set's hash-order happens to
    yield first."""
    span = "Prototyped a GraphQL layer on top of the existing gRPC services,"
    results = {nearest_preceding_token([span], is_relevant=_relevant) for _ in range(50)}
    assert results == {"grpc"}, (
        "must be exactly {'grpc'} every time -- a flaky result here means the "
        "position tracking regressed back to unordered-set behaviour"
    )


def test_nearest_preceding_token_uses_the_most_recent_span_with_any_token():
    """A span with nothing relevant in it must not erase what an earlier
    span established."""
    spans_before = [
        "Deprecated the legacy PHP monolith",
        "while building its replacement in Go",
    ]
    assert nearest_preceding_token(spans_before, is_relevant=_relevant) == "go"


def test_nearest_preceding_token_is_none_when_nothing_precedes_it():
    assert nearest_preceding_token([], is_relevant=_relevant) is None
    assert nearest_preceding_token(["kept the docs well organized"], is_relevant=_relevant) is None


def test_nearest_preceding_token_respects_profile_declared_technologies():
    """is_relevant is caller-supplied so a profile's own declared skill
    (never in the curated TECHNOLOGIES list) can still be found -- exactly
    the same posture `rules.resolve_technology_subject` already takes."""
    is_relevant = lambda token: is_technology(token, declared=frozenset({"zigflow"}))
    span = "Piloted the existing internal Zigflow tooling"
    assert nearest_preceding_token([span], is_relevant=is_relevant) == "zigflow"
