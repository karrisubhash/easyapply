"""Deterministic location matching: what matching_v4 answers, and what it refuses.

Two questions, and no others: is the candidate in the city the posting named,
and failing that, are they in the same country. Everything else a real posting
says about place — a timezone window, a region, a hybrid-attendance policy,
whether someone would relocate — is not evaluated, and the tests that pin those
as UNCERTAIN are as load-bearing as the ones that pin a match.

**MISSING is never a location verdict.** Not caution, accuracy: the profile
schema has no residence field and no relocation field, so "their last job was
in Dublin" is not "they cannot work in Berlin". A whole section below exists to
make that impossible to regress.

The other half of the file is the vocabulary. A curated city table that
resolved an ambiguous name would produce a confident MATCHED on the wrong
continent, which is the error class this project treats as worst — so
`AMBIGUOUS_CITY_NAMES` is asserted to stay unresolvable, and the import-time
guard is proved to actually fire.
"""

from datetime import date

import pytest

from app.models.match import (
    CATEGORY_QUALIFICATION,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_UNCERTAIN,
)
from app.services.matching import geography
from app.services.matching.engine import JobPosting, evaluate
from app.services.matching.evidence import TIER_EMPLOYMENT_DESCRIBED
from app.services.matching.geography import (
    AMBIGUOUS_CITY_NAMES,
    CITY_COUNTRY,
    COUNTRIES,
    COUNTRY_ALIASES,
    Place,
    is_location_requirement,
    normalize_place,
    parse_location_field,
    places_in_text,
)
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.rules import judge_location
from tests.matching_fixtures import (
    AS_OF,
    a_candidate,
    a_skill_row,
    a_version,
    an_education_row,
    an_experience_row,
)


def index(*experiences) -> ProfileIndex:
    return ProfileIndex(a_version(experiences=list(experiences)))


def judge(requirement, *experiences, posting=None, required=True):
    return judge_location(
        description=requirement,
        index=0,
        is_required=required,
        profile=index(*experiences),
        posting=posting,
    )


def at(location, *, start=date(2020, 1, 1), end=None, id=0, company="Acme"):
    return an_experience_row(
        company=company, job_title="Backend Engineer", start=start, end=end,
        location=location, id=id, description="Python services.",
    )


# =============================================================================
# The verdict matrix
# =============================================================================


@pytest.mark.parametrize(
    "candidate_location, requirement, expected, why",
    [
        ("Berlin, Germany", "Based in Berlin", STATUS_MATCHED, "same city"),
        ("Munich, Germany", "Based in Berlin", STATUS_MATCHED, "same country"),
        ("Hamburg, Germany", "Based in Berlin", STATUS_MATCHED, "same country"),
        ("Frankfurt, Germany", "Based in Frankfurt", STATUS_MATCHED, "same city"),
        ("Frankfurt, Germany", "Based in Berlin", STATUS_MATCHED, "same country"),
        ("Berlin, Germany", "Based in London", STATUS_UNCERTAIN, "different country"),
        ("Berlin, Germany", "Based in Dublin", STATUS_UNCERTAIN, "different country"),
        ("Toronto, Canada", "Based in Berlin", STATUS_UNCERTAIN, "different country"),
        ("London, United Kingdom", "Based in Berlin", STATUS_UNCERTAIN, "different country"),
        ("Remote", "Based in Frankfurt", STATUS_UNCERTAIN, "candidate states no place"),
        (None, "Based in Frankfurt", STATUS_UNCERTAIN, "candidate records no location"),
    ],
)
def test_the_verdict_matrix(candidate_location, requirement, expected, why):
    verdict = judge(requirement, at(candidate_location))

    assert verdict.status == expected, f"{candidate_location!r} vs {requirement!r} ({why})"


def test_a_city_match_is_believed_more_firmly_than_a_country_match():
    """Both are MATCHED; they are not equally strong, and the confidence says
    so rather than the verdict pretending they are the same."""
    city = judge("Based in Berlin", at("Berlin, Germany"))
    country = judge("Based in Berlin", at("Hamburg, Germany"))

    assert city.status == country.status == STATUS_MATCHED
    assert city.confidence > country.confidence


def test_a_country_match_says_in_words_that_the_city_differs():
    verdict = judge("Based in Berlin", at("Hamburg, Germany"))

    assert "Hamburg" in verdict.explanation
    assert "Germany" in verdict.explanation
    assert "city differs" in verdict.explanation


# =============================================================================
# MISSING is never a location verdict
# =============================================================================


@pytest.mark.parametrize(
    "candidate_location, requirement",
    [
        ("Toronto, Canada", "Based in Berlin"),
        ("Berlin, Germany", "Based in Tokyo"),
        ("Remote", "Based in Frankfurt"),
        (None, "Based in Frankfurt"),
        ("Berlin, Germany", "Fully remote within the EU"),
        ("Berlin, Germany", "Based in Atlantis"),
        ("Atlantis, Narnia", "Based in Berlin"),
    ],
)
def test_location_is_never_missing(candidate_location, requirement):
    """The profile has no residence field and no relocation field. "Their last
    job was elsewhere" is not "they cannot work here", and a verdict of MISSING
    would assert something the profile never states."""
    verdict = judge(requirement, at(candidate_location))

    assert verdict.status != STATUS_MISSING
    assert verdict.status in {STATUS_MATCHED, STATUS_UNCERTAIN}


def test_an_out_of_area_candidate_is_told_why_it_is_open_rather_than_refused():
    verdict = judge("Based in Berlin", at("Toronto, Canada"))

    assert verdict.status == STATUS_UNCERTAIN
    assert "willing to live" in verdict.explanation
    assert "rather than a refusal" in verdict.explanation


# =============================================================================
# What v4 declines to interpret
# =============================================================================


def test_the_timezone_half_of_a_requirement_is_not_interpreted():
    """"Based in Berlin, or within two hours of CET" is two conditions. The
    first is supported and settles the question when it is met; the second is
    not read at all. A London candidate is inside the stated CET window and is
    still UNCERTAIN, which is what a person reading the same posting concluded."""
    london = judge("Based in Berlin, or within two hours of CET", at("London, United Kingdom"))
    dublin = judge("Based in Berlin, or within two hours of CET", at("Dublin, Ireland"))

    assert london.status == STATUS_UNCERTAIN
    assert dublin.status == STATUS_UNCERTAIN


def test_the_supported_half_of_a_mixed_requirement_still_settles_it():
    berlin = judge("Based in Berlin, or within two hours of CET", at("Berlin, Germany"))
    munich = judge("Based in Berlin, or within two hours of CET", at("Munich, Germany"))

    assert berlin.status == STATUS_MATCHED
    assert munich.status == STATUS_MATCHED


def test_a_region_is_not_a_place():
    """"the EU" is a group of countries. Resolving it would be regional
    matching, which is deferred."""
    verdict = judge("Fully remote within the EU", at("Berlin, Germany"))

    assert verdict.status == STATUS_UNCERTAIN


def test_a_requirement_naming_two_countries_is_refused():
    """Multi-location postings are deferred. Picking one would be inventing
    which office the posting meant."""
    verdict = judge("Based in Berlin or anywhere in France", at("Berlin, Germany"))

    assert verdict.status == STATUS_UNCERTAIN
    assert "more than one country" in verdict.explanation


def test_a_remote_posting_is_not_judged_on_location():
    """Remote eligibility is deferred, so the engine declines rather than
    answering a question it does not understand."""
    verdict = judge(
        "Based in Berlin",
        at("Berlin, Germany"),
        posting=JobPosting(location_raw="Berlin, Germany", workplace_type="remote"),
    )

    assert verdict.status == STATUS_UNCERTAIN
    assert "remote" in verdict.explanation.lower()


def test_a_requirement_naming_no_known_place_falls_back_to_the_postings_location():
    verdict = judge(
        "Based in our head office",
        at("Berlin, Germany"),
        posting=JobPosting(location_raw="Berlin, Germany (hybrid)"),
    )

    assert verdict.status == STATUS_MATCHED
    assert "posting's location" in verdict.explanation


def test_a_requirement_naming_no_place_at_all_is_uncertain():
    verdict = judge("Based in our head office", at("Berlin, Germany"))

    assert verdict.status == STATUS_UNCERTAIN
    assert "names no place" in verdict.explanation


# =============================================================================
# Where the candidate is — ProfileIndex.current_location()
# =============================================================================


def test_an_ongoing_role_is_preferred_over_an_ended_one():
    resolved = index(
        at("Berlin, Germany", start=date(2015, 1, 1), end=date(2020, 1, 1), id=1),
        at("Toronto, Canada", start=date(2021, 1, 1), end=None, id=2),
    ).current_location()

    assert resolved == Place(country="canada", city="toronto")


def test_an_ended_role_is_used_when_nothing_newer_states_a_place():
    resolved = index(
        at("Berlin, Germany", start=date(2015, 1, 1), end=date(2020, 1, 1))
    ).current_location()

    assert resolved == Place(country="germany", city="berlin")


def test_concurrent_roles_in_different_countries_resolve_to_nothing():
    """The profile genuinely does not say where this person is. Picking the
    one that happens to match the posting would be inventing a fact."""
    resolved = index(
        at("Berlin, Germany", end=None, id=1),
        at("Lisbon, Portugal", end=None, id=2),
    ).current_location()

    assert resolved is None


def test_concurrent_roles_in_one_country_keep_the_country_and_drop_the_city():
    """Both rows agree on Germany and disagree on the city, so only the part
    they agree on is asserted."""
    resolved = index(
        at("Berlin, Germany", end=None, id=1),
        at("Munich, Germany", end=None, id=2),
    ).current_location()

    assert resolved == Place(country="germany", city=None)


def test_a_country_only_resolution_can_still_match_on_country():
    verdict = judge(
        "Based in Berlin",
        at("Berlin, Germany", end=None, id=1),
        at("Munich, Germany", end=None, id=2),
    )

    assert verdict.status == STATUS_MATCHED


def test_an_ongoing_remote_role_does_not_block_an_older_located_one():
    """"Remote" states no place, so it is not a conflicting answer — it is no
    answer, and the most recent role that does state one is used."""
    resolved = index(
        at("Remote", start=date(2023, 1, 1), end=None, id=2),
        at("Berlin, Germany", start=date(2015, 1, 1), end=date(2020, 1, 1), id=1),
    ).current_location()

    assert resolved == Place(country="germany", city="berlin")


def test_a_profile_with_no_experiences_has_no_location():
    assert index().current_location() is None


def test_education_and_projects_are_never_read_for_location():
    """A university in Berlin is not an address."""
    version = a_version(
        education=[an_education_row(institution="Humboldt University, Berlin")],
        skills=[a_skill_row("Python")],
    )
    assert ProfileIndex(version).current_location() is None


def test_resolution_is_deterministic_across_runs():
    experiences = [
        at("Berlin, Germany", start=date(2015, 1, 1), end=date(2020, 1, 1), id=1),
        at("Munich, Germany", start=date(2016, 1, 1), end=date(2020, 1, 1), id=2),
    ]
    results = {index(*experiences).current_location() for _ in range(20)}

    assert len(results) == 1


# =============================================================================
# Evidence — §7
# =============================================================================


def test_a_matched_location_cites_the_employment_it_was_read_from():
    verdict = judge("Based in Berlin", at("Berlin, Germany", company="Harborline"))

    assert verdict.status == STATUS_MATCHED
    assert verdict.evidence, "a matched location with no evidence is not allowed"
    item = verdict.evidence[0]
    assert item.source_type == "experience"
    assert item.source_id is not None
    assert item.tier == TIER_EMPLOYMENT_DESCRIBED
    assert item.quote == "Berlin, Germany"
    assert "Harborline" in item.label


def test_a_matched_location_never_cites_the_posting():
    """The requirement asked the question; it is not evidence about the
    candidate."""
    verdict = judge("Based in Berlin", at("Berlin, Germany"))

    for item in verdict.evidence:
        assert item.source_type == "experience"
        assert "Based in Berlin" not in (item.quote or "")


def test_an_uncertain_location_keeps_the_evidence_it_weighed():
    verdict = judge("Based in Berlin", at("Toronto, Canada"))

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence
    assert verdict.evidence[0].quote == "Toronto, Canada"


def test_an_uncertain_location_with_nothing_to_weigh_cites_nothing():
    verdict = judge("Based in Berlin", at(None))

    assert verdict.status == STATUS_UNCERTAIN
    assert verdict.evidence == []


def test_a_location_verdict_stays_in_the_qualification_category():
    """No new match category, so no CHECK-constraint migration and no change
    to how a stored result is read."""
    verdict = judge("Based in Berlin", at("Berlin, Germany"))

    assert verdict.category == CATEGORY_QUALIFICATION


# =============================================================================
# The geography vocabulary
# =============================================================================


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Berlin, Germany", Place("germany", "berlin")),
        ("Frankfurt, Germany", Place("germany", "frankfurt")),
        ("Munich, Germany", Place("germany", "munich")),
        ("London, UK", Place("united kingdom", "london")),
        ("Dublin, Ireland", Place("ireland", "dublin")),
        ("Berlin, Germany (hybrid)", Place("germany", "berlin")),
        ("Zurich, Switzerland", Place("switzerland", "zurich")),
        ("Berlin", Place("germany", "berlin")),
        ("Germany", Place("germany", None)),
    ],
)
def test_supported_location_forms_parse(text, expected):
    assert parse_location_field(text) == expected


@pytest.mark.parametrize(
    "text", ["Remote", "Fully remote", "Anywhere", "Atlantis, Narnia", "", "   ", None]
)
def test_unsupported_location_forms_stay_unknown(text):
    assert parse_location_field(text) is None


def test_accents_fold_so_one_city_is_one_key():
    assert parse_location_field("Zürich, Switzerland") == Place("switzerland", "zurich")
    assert parse_location_field("São Paulo, Brazil") == Place("brazil", "sao paulo")
    assert normalize_place("MÜNCHEN") == "munchen"


def test_a_city_and_country_that_contradict_each_other_resolve_to_nothing():
    """"Berlin, France" is a contradiction. Picking a side would be guessing
    which half of the string to believe."""
    assert parse_location_field("Berlin, France") is None


def test_an_unlisted_city_is_unknown_rather_than_wrong():
    """The table is finite by design. A city it has never heard of produces
    UNCERTAIN, never a confident answer."""
    assert parse_location_field("Ulaanbaatar, Mongolia") is None
    assert judge("Based in Ulaanbaatar", at("Berlin, Germany")).status == STATUS_UNCERTAIN


@pytest.mark.parametrize("name", sorted(AMBIGUOUS_CITY_NAMES))
def test_an_ambiguous_city_name_never_resolves(name):
    """"Cambridge" is a real employment market in two countries. Guessing
    would produce a confident MATCHED on the wrong continent."""
    assert name not in CITY_COUNTRY
    assert parse_location_field(name) is None


def test_the_vocabulary_guard_actually_fires():
    """The guard is only worth having if a bad entry would be caught."""
    with pytest.raises(AssertionError, match="not internally consistent"):
        original = dict(geography.CITY_COUNTRY)
        try:
            geography.CITY_COUNTRY["cambridge"] = "united kingdom"
            geography.assert_vocabulary_is_unambiguous()
        finally:
            geography.CITY_COUNTRY.clear()
            geography.CITY_COUNTRY.update(original)
    # And the real table still passes, restored.
    geography.assert_vocabulary_is_unambiguous()


def test_the_vocabulary_is_internally_consistent():
    geography.assert_vocabulary_is_unambiguous()
    assert set(CITY_COUNTRY.values()) <= COUNTRIES
    assert set(COUNTRY_ALIASES.values()) <= COUNTRIES
    assert not set(CITY_COUNTRY) & set(AMBIGUOUS_CITY_NAMES)


def test_two_letter_country_forms_are_not_scanned_for_in_prose():
    """"Must be authorized to work in the US" is a work-authorisation
    requirement. Reading its "US" as a country would turn it into a location
    one, which this milestone deliberately leaves alone."""
    found = places_in_text("Must be legally authorised to work in the US")

    assert found.countries == frozenset()
    assert not is_location_requirement("Must be legally authorised to work in the US")


@pytest.mark.parametrize(
    "text",
    [
        "Must be legally authorised to work in the EU",
        "Right to work in Portugal",
        "Right to work in the UK",
        "Participation in an on-call rotation",
        "Remote debugging experience",
        "Hybrid cloud architecture",
    ],
)
def test_a_non_location_qualification_is_not_treated_as_one(text):
    assert not is_location_requirement(text)


def test_the_recogniser_matches_the_structurers_own_lead_ins():
    """Matching keeps its own copy of the phrasings the job structurer files
    location lines by, the way `rules._DEGREE_PATTERNS` keeps its own degree
    vocabulary. They must stay in step: a line the structurer files as a
    location that the matcher does not recognise would be answered as an
    ordinary qualification, silently."""
    from app.services.job.structuring import _LOCATION_LEAD

    assert _LOCATION_LEAD.pattern == geography.LOCATION_LEAD.pattern


# =============================================================================
# End to end through the engine
# =============================================================================


def a_posting_with_a_location(line="Based in Berlin"):
    return a_candidate(required_skills=["Python"], qualifications=[(line, True)])


def test_a_location_requirement_is_judged_rather_than_deferred():
    evaluation = evaluate(
        a_posting_with_a_location(),
        a_version(skills=[a_skill_row("Python")], experiences=[at("Berlin, Germany")]),
        as_of=AS_OF,
        posting=JobPosting(location_raw="Berlin, Germany"),
    )
    verdicts = {j.requirement_label: j for j in evaluation.judgments}

    assert verdicts["Based in Berlin"].status == STATUS_MATCHED
    assert verdicts["Python"].status == STATUS_MATCHED


def test_work_authorisation_is_still_answered_as_an_ordinary_qualification():
    """The two live in the same list. Telling them apart is the whole point of
    the recogniser, and getting it wrong in either direction is a regression."""
    evaluation = evaluate(
        a_candidate(qualifications=[("Must be legally authorised to work in the EU", True)]),
        a_version(experiences=[at("Berlin, Germany")]),
        as_of=AS_OF,
    )
    verdict = evaluation.judgments[0]

    assert verdict.status == STATUS_UNCERTAIN
    assert "does not evaluate free-text qualifications" in verdict.explanation


def test_a_matched_location_counts_toward_required_coverage():
    """Location participates through the existing coverage mechanism. No
    special score, no new axis."""
    evaluation = evaluate(
        a_posting_with_a_location(),
        a_version(skills=[a_skill_row("Python")], experiences=[at("Berlin, Germany")]),
        as_of=AS_OF,
    )

    assert evaluation.score.required.total == 2
    assert evaluation.score.required.matched == 2
    assert evaluation.score.required.uncertain == 0


def test_an_uncertain_location_dilutes_coverage_without_counting_as_missing():
    evaluation = evaluate(
        a_posting_with_a_location(),
        a_version(skills=[a_skill_row("Python")], experiences=[at("Toronto, Canada")]),
        as_of=AS_OF,
    )

    assert evaluation.score.required.matched == 1
    assert evaluation.score.required.uncertain == 1
    assert evaluation.score.required.missing == 0


def test_the_engine_works_without_a_posting_context():
    """`posting` is optional, so every existing caller keeps working — the
    requirement's own words are then the only source of a place."""
    evaluation = evaluate(
        a_posting_with_a_location(),
        a_version(experiences=[at("Berlin, Germany")]),
        as_of=AS_OF,
    )

    assert evaluation.judgments[-1].status == STATUS_MATCHED


def test_the_result_is_reproducible():
    args = (
        a_posting_with_a_location(),
        a_version(skills=[a_skill_row("Python")], experiences=[at("Berlin, Germany")]),
    )
    posting = JobPosting(location_raw="Berlin, Germany", workplace_type="hybrid")
    first = evaluate(*args, as_of=AS_OF, posting=posting)
    second = evaluate(*args, as_of=AS_OF, posting=posting)

    assert first.judgments == second.judgments
    assert first.algorithm_version == second.algorithm_version == "matching_v5_1"
