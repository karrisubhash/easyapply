"""Canonical tokens: what collapses into one, and what must never.

Normalization is the highest-leverage place in this engine to be wrong. An
alias that claims two unrelated technologies are the same produces a confident
MATCHED against a skill the candidate does not have — a failure that, unlike a
miss, is invisible to the person reading the result.

So this file is two halves of equal weight. The first pins the collapses the
rules promise. The second pins the NON-collapses: the pairs that stay distinct
because nothing claims otherwise, which is the property the whole design rests
on and the one a careless alias addition would silently break.

Pure functions throughout. No session, no application, no clock.
"""

import pytest

from app.services.matching.normalization import (
    ALIASES,
    canonical,
    canonical_words,
    is_alias_of,
    phrase_variants,
)


# --- typography is not meaning -------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Python", "python"),
        ("python", "python"),
        ("PYTHON", "python"),
        ("  Python  ", "python"),
        ("Python\t\n", "python"),
        ("Postgre SQL", "postgresql"),
        ("  Amazon   Web    Services  ", "aws"),
    ],
)
def test_case_and_whitespace_are_typography(raw, expected):
    assert canonical(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Node.js", "node.js"),
        ("React.js", "react"),
        ("CI/CD", "ci/cd"),
        ("Objective-C", "objective c"),
        ("Scikit-learn", "scikit learn"),
    ],
)
def test_punctuation_is_a_separator_not_a_deletion(raw, expected):
    """`react/redux` must become two words, never the single token
    `reactredux` — which is what stripping rather than separating would give."""
    assert canonical(raw) == expected


@pytest.mark.parametrize("raw", ["C++", "c++", "C#", "c#", ".NET"])
def test_symbols_that_are_part_of_a_name_survive(raw):
    """`+` and `#` are kept because they are the name, not punctuation around
    it. Losing them would collapse C, C++ and C# into one token."""
    token = canonical(raw)
    assert token in {"c++", "c#", ".net"}
    assert canonical("C") != canonical("C++") != canonical("C#")


# --- versions are versions -----------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Python 3", "python"),
        ("Python 3.11", "python"),
        ("Java 11", "java"),
        ("Angular 2+", "angular"),
        ("Vue 3", "vue"),
        ("Angular 2.1+", "angular"),
        ("React v18", "react"),
    ],
)
def test_a_trailing_version_is_stripped(raw, expected):
    assert canonical(raw) == expected


@pytest.mark.parametrize("raw", ["3D modelling", "Web 2.0 consulting"])
def test_a_number_inside_a_phrase_is_not_a_version(raw):
    """The version pattern is anchored at the end for this reason: a numeral
    in the middle of a phrase is part of what the phrase says."""
    assert canonical(raw) not in {"d modelling", "web consulting", ""}
    assert "3d" in canonical(raw) or "2" in canonical(raw)


# --- the alias table ------------------------------------------------------


@pytest.mark.parametrize(
    "left, right",
    [
        ("PostgreSQL", "Postgres"),
        ("PostgreSQL", "postgresql"),
        ("PostgreSQL", "psql"),
        ("Kubernetes", "K8s"),
        ("Go", "Golang"),
        ("Node.js", "NodeJS"),
        ("Node.js", "node"),
        ("JavaScript", "JS"),
        ("TypeScript", "TS"),
        ("AWS", "Amazon Web Services"),
        ("GCP", "Google Cloud Platform"),
        ("Azure", "Microsoft Azure"),
        ("MongoDB", "Mongo"),
        ("React", "React.js"),
        ("C#", "C Sharp"),
        ("REST", "RESTful API"),
    ],
)
def test_supported_aliases_collapse(left, right):
    assert is_alias_of(left, right)


def test_every_alias_key_is_already_normalized():
    """The table is consulted AFTER the mechanical rules, so a key that is not
    itself in canonical form is an entry that can never fire. Cheap to state
    here; expensive to notice in production."""
    for key in ALIASES:
        assert key == key.casefold().strip(), key
        assert "  " not in key, key


def test_no_alias_points_at_another_alias():
    """One hop only. A chain would make the result depend on lookup order,
    which is exactly the kind of non-determinism this engine must not have."""
    for key, target in ALIASES.items():
        assert target not in ALIASES, f"{key} -> {target} -> {ALIASES.get(target)}"


# --- what must NOT collapse ----------------------------------------------
#
# The most important test in this file. Each pair is a real mistake a
# similarity-based matcher makes, and each one here is prevented not by a
# special case but by the absence of a claim.


@pytest.mark.parametrize(
    "left, right",
    [
        ("Python", "Django"),
        ("React", "React Native"),
        ("PostgreSQL", "MySQL"),
        ("Java", "JavaScript"),
        ("AWS", "Azure"),
        ("C", "C++"),
        ("C++", "C#"),
        ("Go", "Django"),
        ("Angular", "AngularJS build tooling"),
        ("SQL", "PostgreSQL"),
        ("Node.js", "Next.js"),
        ("Vue", "Nuxt"),
        ("Kubernetes", "Docker"),
        ("TensorFlow", "PyTorch"),
    ],
)
def test_different_things_stay_different(left, right):
    assert not is_alias_of(left, right)
    assert canonical(left) != canonical(right)


def test_an_empty_name_is_no_token_rather_than_a_matching_one():
    """Two blanks must not match each other. A requirement that normalizes to
    nothing is unanswerable, not universally answered."""
    assert canonical("") == ""
    assert canonical(None) == ""
    assert canonical("   ") == ""
    assert canonical("///") == ""
    assert not is_alias_of("", "")
    assert not is_alias_of(None, "   ")


# --- scanning prose -------------------------------------------------------


def test_words_are_split_on_boundaries_never_on_substrings():
    """`go` must not be found inside `Django` or `MongoDB`. This is what makes
    short canonical tokens safe here."""
    words = canonical_words("Django and MongoDB and Google")
    assert "go" not in words
    assert {"django", "mongodb", "google"} <= set(words)


def test_phrase_variants_find_a_single_word():
    assert "python" in phrase_variants("Built a Python service")


def test_phrase_variants_find_a_multi_word_alias():
    """Adjacent pairs and triples are what let a phrase inside a sentence
    reach its alias entry."""
    assert "aws" in phrase_variants("Deployed on Amazon Web Services daily")
    assert "gcp" in phrase_variants("We migrated to Google Cloud Platform in 2023")
    assert "rest" in phrase_variants("Django REST API development")


def test_phrase_variants_do_not_join_distant_words():
    """Adjacent windows only. An unordered-subset search is how `Java` and a
    later `Script` become `javascript` — a fabricated skill from two real
    words."""
    variants = phrase_variants("Experience with Java, and separately with Script tooling")
    assert "javascript" not in variants
    assert "java" in variants


def test_prose_scanning_keeps_a_technology_distinct_from_its_framework():
    variants = phrase_variants("Maintained a Django monolith")
    assert "django" in variants
    assert "python" not in variants


def test_phrase_variants_of_nothing_is_empty():
    assert phrase_variants("") == set()
    assert phrase_variants(None) == set()
