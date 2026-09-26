"""The non-doing marker vocabulary, and the names it must not read markers out of.

`is_non_doing` decides whether a clause says a technology was NOT used. Getting
it wrong in the permissive direction costs an UNCERTAIN; getting it wrong in
the other direction silently deletes the best evidence a profile has, which is
what happened here.

The defect these tests pin: the function promised word-boundary matching and
tokenised on `[a-z]+`. A hyphen is not in that class, so "scikit-learn"
arrived as `{"scikit", "learn"}`, "learn" is a marker, and the whole clause was
graded as a disclaimer. "Demand forecasting models for logistics clients, in
Python and scikit-learn" — a sentence about work that was done — demoted its
author's Python from `declared_corroborated` to `declared_only`.

It survived three algorithm versions because nothing looked: `is_non_doing` had
no direct test, and the unit fixture for that very profile wrote the sentence
as "...in Python." with the scikit-learn dropped. Of the only two non-doing
suppressions in the whole real-data benchmark, this was one.
"""

import pytest

from app.services.matching import vocabulary
from app.services.matching.vocabulary import (
    MARKER_BEARING_NAMES,
    NON_DOING_MARKERS,
    TECHNOLOGIES,
    is_non_doing,
)


# =============================================================================
# The collision: a marker word inside a name
# =============================================================================


@pytest.mark.parametrize(
    "sentence",
    [
        # The exact benchmark sentence that produced the defect.
        "Demand forecasting models for logistics clients, in Python and scikit-learn",
        # The same name, every separator prose actually uses.
        "Demand forecasting models in Python and scikit learn",
        "Shipped a Scikit-Learn model to production on AWS",
        "scikit-learn and pandas for the feature pipeline",
    ],
)
def test_scikit_learn_is_a_library_name_not_a_claim_about_learning(sentence):
    """`learn` is a marker because "currently learning Rust" is a claim about
    the future. It is not a marker inside the name of the library."""
    assert is_non_doing(sentence) is False


@pytest.mark.parametrize(
    "sentence",
    [
        "Machine learning pipelines in Python and Spark",
        "Deep learning research using PyTorch",
        "Reinforcement learning agents trained in simulation",
        "Unsupervised learning over clickstream data with Spark",
        "Transfer learning on top of a pretrained model",
        "Built a learning management system in Django",
        # Case is not a defence; the function casefolds first.
        "MACHINE LEARNING pipelines in Python",
        "Machine-learning pipelines in Python",
    ],
)
def test_a_field_whose_name_contains_learning_is_not_a_disclaimer(sentence):
    """"Machine learning" names what the work was about. Reading the marker out
    of it inverts the sentence — and would demote every technology named
    alongside it in the same clause."""
    assert is_non_doing(sentence) is False


def test_the_longest_name_wins_so_a_prefix_cannot_consume_it():
    """"unsupervised learning" must not be matched as the "supervised
    learning" inside it and leave "un" behind."""
    assert is_non_doing("Unsupervised learning on Spark") is False
    assert is_non_doing("Supervised learning on Spark") is False


# =============================================================================
# The markers still have to work
# =============================================================================


@pytest.mark.parametrize(
    "sentence",
    [
        "Currently learning Rust in my spare time",
        "Learning Kubernetes through an online course",
        "Keen to learn Go",
        "Some exposure to Kafka",
        "Familiar with Terraform",
        "Studying for the AWS certification",
    ],
)
def test_a_genuine_learn_claim_still_fires(sentence):
    """The reason `learn` and `learning` are in the list at all. Neutralising
    the names must not neutralise the word."""
    assert is_non_doing(sentence) is True


def test_the_evaluated_and_cancelled_case_is_unchanged():
    """The sentence the marker list was written for, from the benchmark's
    front-end profile. It is the other of the only two suppressions in the
    dataset, and it is the correct one."""
    assert (
        is_non_doing(
            "Evaluated React Native for a companion mobile app, but the project "
            "was cancelled before any code shipped"
        )
        is True
    )
    assert is_non_doing("evaluated React Native; project was cancelled") is True


@pytest.mark.parametrize(
    "sentence",
    [
        "We are migrating away from Angular",
        "Moving away from Jenkins to GitHub Actions",
        "Prototyped a Svelte rewrite that never shipped",
        "A deprecated Rails service",
        "Considered Kafka and rejected it",
    ],
)
def test_the_rest_of_the_marker_list_is_untouched(sentence):
    assert is_non_doing(sentence) is True


# =============================================================================
# Hyphenation: opaque only when it names a technology
# =============================================================================


def test_a_hyphenated_marker_that_is_not_a_technology_still_splits():
    """The trap in fixing this by joining hyphens. "re-evaluated" is an
    evaluation; only the curated vocabulary may silence a marker, so a
    hyphenated compound that names nothing keeps its parts."""
    assert is_non_doing("Re-evaluated Kafka and dropped it") is True
    assert is_non_doing("Co-designed a migration that was cancelled") is True


def test_a_hyphenated_technology_name_is_read_whole():
    """Requirement: a hyphenated technology name is one expression, not the
    words inside it."""
    assert vocabulary._claim_words("scikit-learn") == set()
    assert "learn" not in vocabulary._claim_words("we used scikit-learn here")
    # Not a technology, so both the compound and its parts are still claims.
    assert vocabulary._claim_words("re-evaluated") == {"re-evaluated", "re", "evaluated"}


def test_a_marker_is_still_never_matched_as_a_substring():
    """The invariant the docstring has always promised: "planning" must not
    fire on "unplanned", and "learn" must not fire on "learned lessons"."""
    assert is_non_doing("An unplanned outage in the Redis cluster") is False
    assert is_non_doing("Wrote up learned lessons for the Django team") is False


# =============================================================================
# Separators: a name must not be steppable-around
# =============================================================================
#
# Neutralising a name is only worth doing if it cannot be bypassed by writing
# the name with a different separator. `[\s-]+` covers the ASCII hyphen and,
# since `\s` is Unicode, every Zs space including NBSP — but not the Pd dashes
# and not the Cf invisibles. Résumés arrive as extracted PDF and scraped HTML,
# where all of these occur, and each one reopened the original collision.

#: (label, separator) for every variant the audit found, plus the ones that
#: already worked, so a future change cannot silently lose them either.
SEPARATORS = [
    ("ascii hyphen U+002D", "-"),
    ("space", " "),
    ("NBSP U+00A0", " "),
    ("narrow NBSP U+202F", " "),
    ("en quad U+2000", " "),
    ("ideographic space U+3000", "　"),
    ("soft hyphen U+00AD", "­"),
    ("hyphen U+2010", "‐"),
    ("non-breaking hyphen U+2011", "‑"),
    ("figure dash U+2012", "‒"),
    ("en dash U+2013", "–"),
    ("em dash U+2014", "—"),
    ("horizontal bar U+2015", "―"),
    ("minus sign U+2212", "−"),
    ("zero-width space U+200B", "​"),
]


@pytest.mark.parametrize("label, separator", SEPARATORS, ids=[s[0] for s in SEPARATORS])
def test_a_library_name_survives_every_separator(label, separator):
    """scikit-learn, however the document happened to spell the hyphen."""
    assert is_non_doing(f"Demand forecasting in Python and scikit{separator}learn") is False


@pytest.mark.parametrize("label, separator", SEPARATORS, ids=[s[0] for s in SEPARATORS])
def test_a_field_name_survives_every_separator(label, separator):
    """machine learning, however the document happened to spell the space."""
    assert is_non_doing(f"Machine{separator}learning pipelines in Python and Spark") is False


@pytest.mark.parametrize(
    "label, separator",
    [("double space", "  "), ("tab", "\t"), ("NBSP", " "), ("newline", "\n"),
     ("mixed run", " \t ")],
    ids=["double space", "tab", "NBSP", "newline", "mixed run"],
)
def test_a_multiword_marker_survives_any_whitespace_run(label, separator):
    """The multi-word markers are matched as literal substrings carrying one
    space, so every other spelling of whitespace used to slip past them. That
    predated the name neutralisation; the same fold closes it."""
    assert is_non_doing(f"We are migrating{separator}away from Angular") is True


def test_the_fold_does_not_rescue_a_marker_from_itself():
    """Removing a soft hyphen rejoins the word it was breaking. A marker
    hyphenated across a PDF line break is still a marker."""
    assert is_non_doing("We eval­uated Kafka and dropped it") is True
    assert is_non_doing("The project was can­celled") is True


def test_the_fold_is_inert_for_ordinary_ascii_text():
    """The correction must change nothing for text that never carried an
    exotic separator — which is every sentence in the benchmark. Asserted on
    the fold itself, so it cannot pass by both sides being equally wrong."""
    for sentence in [
        "Evaluated React Native for a companion mobile app, but the project was cancelled",
        "Demand forecasting models for logistics clients, in Python and scikit-learn",
        "Machine learning pipelines in Python and Spark",
        "Currently learning Rust in my spare time",
        "Re-evaluated Kafka and dropped it",
        "An unplanned outage in the Redis cluster",
    ]:
        folded = sentence.casefold().translate(vocabulary._SEPARATOR_FOLD)
        assert folded == sentence.casefold()
        assert vocabulary._WHITESPACE_RUN.sub(" ", folded) == folded


def test_the_fold_covers_exactly_the_characters_the_audit_named():
    """Pinned so the table cannot quietly grow or shrink. Dashes become an
    ASCII hyphen; the invisibles are removed."""
    assert {chr(k) for k, v in vocabulary._SEPARATOR_FOLD.items() if v == "-"} == set(
        "‐‑‒–—―−"
    )
    assert {chr(k) for k, v in vocabulary._SEPARATOR_FOLD.items() if v is None} == set(
        "­​"
    )


# =============================================================================
# The import guard
# =============================================================================


def test_no_marker_word_is_readable_inside_a_technology_name():
    """Enforced at import by `assert_markers_do_not_shadow_names`, asserted
    here so the reason is visible in the suite too."""
    single = {marker for marker in NON_DOING_MARKERS if " " not in marker}
    assert single & TECHNOLOGIES == set()

    unguarded = {
        name
        for name in TECHNOLOGIES
        if " " in name and set(name.split()) & single and name not in MARKER_BEARING_NAMES
    }
    assert unguarded == set()


def test_the_import_guard_actually_fires():
    """Worth having only if it would catch the entry that got through. Proved
    by handing it bad sets rather than by mutating this module's globals."""
    with pytest.raises(AssertionError, match="collide with the technology vocabulary"):
        vocabulary.assert_markers_do_not_shadow_names(
            markers={"redis"}, technologies={"redis"}, names=frozenset()
        )

    # The case that actually shipped: a multi-word technology carrying a
    # marker word, with nothing neutralising it.
    with pytest.raises(AssertionError, match="not in\\s+MARKER_BEARING_NAMES"):
        vocabulary.assert_markers_do_not_shadow_names(
            markers={"learn"}, technologies={"scikit learn"}, names=frozenset()
        )

    # And passes for the real vocabulary, which is the state import left it in.
    vocabulary.assert_markers_do_not_shadow_names()


def test_marker_bearing_names_are_not_technology_subjects():
    """Neutralising a name for non-doing analysis must not make it resolvable
    as a requirement's subject — "machine learning" is a field, and
    `is_technology` answering True for it is a different decision."""
    for name in MARKER_BEARING_NAMES:
        if name == "scikit learn":
            continue  # a real library, and already curated as one
        assert name not in TECHNOLOGIES
