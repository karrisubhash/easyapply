"""matching_v5_1: each correction, pinned by the probe that found it.

Every case below was a verdict matching_v5 produced that the profile does not
support, found by an offline probe of the pure rules. Each has its control
beside it — the nearby shape that must NOT move — because a correction that
also moves the control is a different change from the one being made.

The negation half of v5_1 is pinned in tests/test_matching_negation.py; the
benchmark half (matching_real_v1 and declared_only_v1 scoring exactly what v5
scored) in tests/test_matching_evidence_representation.py and
tests/test_declared_only_benchmarks.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import Education, MatchEvidence, MatchRequirementResult
from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.matching import rules
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.service import create_match
from tests.matching_fixtures import (
    AS_OF,
    a_candidate,
    a_skill_row,
    a_structured_job,
    a_version,
    an_experience_row,
    persisted_profile_version,
)


def _experience(version, description: str, years: float | None):
    return rules.judge_experience(
        description=description,
        minimum_years=years,
        index=0,
        is_required=True,
        profile=ProfileIndex(version),
        as_of=AS_OF,
    )


# --- a field of work is confirmed by what names it, not by any one word -----


@pytest.mark.parametrize(
    "title, description, requirement",
    [
        # "development" alone: business development is not software development
        (
            "Business Development Manager",
            "Grew enterprise accounts across the region.",
            "5+ years of software development experience",
        ),
        # "engineering" alone: mechanical engineering is not software engineering
        (
            "Mechanical Engineer",
            "Led engineering reviews for turbine assemblies.",
            "5+ years of software engineering experience",
        ),
        # a denial is not a confirmation
        (
            "Support Agent",
            "Handled customer tickets. No software engineering experience.",
            "5+ years of software engineering experience",
        ),
    ],
)
def test_a_field_is_not_confirmed_by_one_shared_word_or_by_a_denial(title, description, requirement):
    version = a_version(
        experiences=[
            an_experience_row(
                job_title=title, description=description, start=date(2012, 1, 1), end=None
            )
        ]
    )
    judgment = _experience(version, requirement, 5)
    assert judgment.status == STATUS_UNCERTAIN, judgment.explanation


@pytest.mark.parametrize(
    "title, requirement",
    [
        ("Software Engineer", "5+ years of software engineering experience"),
        ("Senior Software Developer", "5+ years of software development experience"),
        ("Frontend Engineer, web front-ends", "4+ years building production web front-ends"),
    ],
)
def test_a_role_that_names_the_field_still_confirms_it(title, requirement):
    """The control: the words that DO name a field still do, in any of the
    forms a title writes them in ("Engineer" for "engineering")."""
    version = a_version(
        experiences=[
            an_experience_row(
                job_title=title, description="Built payment services.", start=date(2015, 1, 1), end=None
            )
        ]
    )
    assert _experience(version, requirement, 4).status == STATUS_MATCHED


# --- years that have not happened are not counted ----------------------------


def test_an_end_date_after_as_of_counts_only_the_time_that_has_passed():
    version = a_version(
        experiences=[
            an_experience_row(
                description="Built REST APIs with Django.",
                start=date(2024, 1, 1),
                end=date(2028, 1, 1),  # a fixed-term contract's agreed end
            )
        ]
    )
    judgment = _experience(version, "3+ years of Django", 3)
    # 2024-01 to AS_OF is under three years; the contract's remaining term is not experience.
    assert judgment.status == STATUS_MISSING, judgment.explanation


def test_a_role_that_has_not_started_contributes_nothing():
    version = a_version(
        experiences=[
            an_experience_row(
                description="Will build Django services.", start=date(2027, 1, 1), end=None
            )
        ]
    )
    assert _experience(version, "3+ years of professional experience", 3).status == STATUS_MISSING


# --- a degree in progress is not held ----------------------------------------


def _degree(end: date) -> Education:
    return Education(
        id=901,
        institution="University of Bristol",
        degree="BSc",
        field_of_study="Computer Science",
        start_date=date(end.year - 4, 1, 1),
        end_date=end,
    )


def test_a_degree_that_ends_after_as_of_is_uncertain_not_held():
    version = a_version(education=[_degree(date(2027, 6, 1))])
    judgment = rules.judge_education(
        description="Bachelor's degree in Computer Science",
        degree_level_required="bachelor",
        field_required=None,
        index=0,
        is_required=True,
        profile=ProfileIndex(version),
        as_of=AS_OF,
    )
    assert judgment.status == STATUS_UNCERTAIN
    assert "in progress" in judgment.explanation


def test_a_completed_degree_is_still_held():
    version = a_version(education=[_degree(date(2018, 6, 1))])
    judgment = rules.judge_education(
        description="Bachelor's degree in Computer Science",
        degree_level_required="bachelor",
        field_required=None,
        index=0,
        is_required=True,
        profile=ProfileIndex(version),
        as_of=AS_OF,
    )
    assert judgment.status == STATUS_MATCHED


# --- several technologies: all of them, or any one ---------------------------


def test_and_requires_every_named_technology():
    version = a_version(
        experiences=[an_experience_row(description="Ran Kubernetes clusters.", start=date(2016, 1, 1), end=None)]
    )
    judgment = _experience(version, "5+ years with Kubernetes and Go", 5)
    assert judgment.status == STATUS_MISSING
    assert "go" in judgment.explanation.lower()


def test_or_accepts_any_named_technology():
    version = a_version(
        experiences=[an_experience_row(description="Ran Kubernetes clusters.", start=date(2016, 1, 1), end=None)]
    )
    assert _experience(version, "5+ years with Kubernetes or Go", 5).status == STATUS_MATCHED


def test_and_is_matched_when_every_technology_is_shown():
    version = a_version(
        experiences=[
            an_experience_row(description="Ran Kubernetes clusters written in Go.", start=date(2016, 1, 1), end=None)
        ]
    )
    assert _experience(version, "5+ years with Kubernetes and Go", 5).status == STATUS_MATCHED


def test_a_technology_inside_a_longer_one_is_not_a_second_subject():
    """"django" inside "django rest framework" is one requirement, not two."""
    version = a_version(skills=[a_skill_row("Django REST Framework")])
    subjects, _ = rules.resolve_technology_subjects(
        "3+ years with Django REST Framework", ProfileIndex(version)
    )
    assert subjects == ["django rest framework"]


# --- one- and two-letter technologies must be written as themselves ----------


@pytest.mark.parametrize(
    "requirement",
    [
        "5+ years of R&D experience",
        "3+ years in C-level stakeholder management",
        "Ships features that go to production weekly",
    ],
)
def test_a_letter_inside_another_word_is_not_a_technology_subject(requirement):
    version = a_version(skills=[a_skill_row("C"), a_skill_row("R"), a_skill_row("Go")])
    subjects, _ = rules.resolve_technology_subjects(requirement, ProfileIndex(version))
    assert not {"c", "r", "go"} & set(subjects), subjects


@pytest.mark.parametrize("requirement, token", [("5+ years of Go", "go"), ("3+ years of C", "c")])
def test_a_short_technology_written_as_a_word_still_resolves(requirement, token):
    version = a_version(skills=[a_skill_row("C"), a_skill_row("Go")])
    subjects, _ = rules.resolve_technology_subjects(requirement, ProfileIndex(version))
    assert subjects == [token]


# --- stated years and a short attributed total do not make a MISSING ---------


def test_stated_years_beyond_a_short_attributed_total_are_uncertain_not_missing():
    version = a_version(
        skills=[a_skill_row("Django", years=4)],
        experiences=[
            an_experience_row(description="Built REST APIs with Django.", start=date(2025, 1, 1), end=None),
            an_experience_row(description="Other work.", start=date(2016, 1, 1), end=date(2024, 12, 1)),
        ],
    )
    assert _experience(version, "3+ years of Django", 3).status == STATUS_UNCERTAIN


def test_a_short_attributed_total_with_no_stated_years_is_still_missing():
    version = a_version(
        experiences=[
            an_experience_row(description="Built REST APIs with Django.", start=date(2025, 1, 1), end=None)
        ]
    )
    assert _experience(version, "3+ years of Django", 3).status == STATUS_MISSING


# --- duration evidence shows the sentence that tied the role -----------------


def test_duration_evidence_quotes_the_sentence_that_named_the_technology():
    version = a_version(
        experiences=[
            an_experience_row(description="Built REST APIs with Django.", start=date(2019, 1, 1), end=None)
        ]
    )
    judgment = _experience(version, "3+ years of Django", 3)
    assert judgment.status == STATUS_MATCHED
    assert [item.quote for item in judgment.evidence] == ["Built REST APIs with Django"]
    assert judgment.evidence[0].detail.startswith("2019-01-01")


# --- stored results ------------------------------------------------------------


def test_a_long_certification_line_no_longer_makes_the_job_unmatchable(db, user):
    """A 150-character key used to raise DataError on INSERT (String(128))."""
    version = persisted_profile_version(db, user, skills=[("Python", None)])
    line = (
        "Security certification such as CISSP, CISM, CISA, OSCP, GIAC Security Essentials, "
        "CompTIA Security+ or an equivalent industry-recognised credential in the field"
    )
    long_experience = "3+ years of " + "distributed systems engineering, " * 8 + "Python"
    job, _ = a_structured_job(
        db,
        user,
        candidate=a_candidate(
            certifications=[(line, True)],
            required_experience=[(long_experience, 3.0)],
        ),
    )
    match, created = create_match(
        db, user_id=user.id, job_id=job.id, profile_version_id=version.id, as_of=AS_OF
    )
    assert created
    rows = db.query(MatchRequirementResult).filter_by(match_result_id=match.id).all()
    assert rows and all(len(row.requirement_label) <= 255 for row in rows)
    assert all(row.requirement_key is None or len(row.requirement_key) <= 128 for row in rows)


def test_evidence_under_a_verdict_that_is_not_matched_is_not_stored_as_qualifying(db, user):
    """A skills-list Python against "3+ years of Python" is UNCERTAIN. Its
    evidence's KIND meets the posting's bar, and it used to be stored as
    qualifying with a note saying so, beside the UNCERTAIN."""
    version = persisted_profile_version(db, user, skills=[("Python", None)])
    job, _ = a_structured_job(
        db, user, candidate=a_candidate(required_experience=[("3+ years of Python", 3.0)])
    )
    match, _ = create_match(
        db, user_id=user.id, job_id=job.id, profile_version_id=version.id, as_of=AS_OF
    )
    row = db.query(MatchRequirementResult).filter_by(match_result_id=match.id).one()
    assert row.status == STATUS_UNCERTAIN
    evidence = db.query(MatchEvidence).filter_by(match_requirement_result_id=row.id).all()
    assert evidence
    assert all(item.qualifies is False for item in evidence)
    assert all("demonstrates this" not in (item.note or "") for item in evidence)


# --- found by the adversarial review of the first v5_1 draft ---------------------
#
# Each of these was a defect in matching_v5_1 as first written, found by probing
# it before release; the test pins the corrected behaviour.


def _only_kubernetes():
    return a_version(
        experiences=[an_experience_row(description="Ran Kubernetes clusters.", start=date(2016, 1, 1), end=None)]
    )


@pytest.mark.parametrize(
    "requirement",
    [
        "5 or more years of experience with Kubernetes and Go",
        "5+ years with Kubernetes and Go, or equivalent experience",
        "Experience with Kubernetes and Go in production or staging environments",
    ],
)
def test_an_or_that_does_not_join_the_technologies_does_not_make_them_alternatives(requirement):
    """The first draft read ANY "or" in the sentence as "either will do"."""
    years = 5 if "years" in requirement else None
    assert _experience(_only_kubernetes(), requirement, years).status == STATUS_MISSING


@pytest.mark.parametrize(
    "requirement",
    ["5+ years with Kubernetes or Go", "5+ years of Kubernetes/Go", "5+ years with Go, Docker, or Kubernetes"],
)
def test_an_or_or_slash_joining_the_technologies_makes_them_alternatives(requirement):
    assert _experience(_only_kubernetes(), requirement, 5).status == STATUS_MATCHED


def test_a_mixed_and_or_list_needs_all_of_them():
    """"Kubernetes and Python or Go" is ambiguous; ambiguity never credits."""
    assert (
        _experience(_only_kubernetes(), "5+ years with Kubernetes and Python or Go", 5).status
        == STATUS_MISSING
    )


@pytest.mark.parametrize(
    "requirement, token",
    [
        ("5+ years of Golang", "go"),
        ("3+ years of continuous integration", "ci"),
        ("3+ years of CSharp", "c#"),
        ("3+ years of Python/Go", "go"),
        ("3+ years of Go-based microservices", "go"),
    ],
)
def test_a_short_token_spelled_long_or_joined_by_punctuation_still_resolves(requirement, token):
    """The short-subject guard must refuse "R&D" and "C-level", not "Golang",
    "continuous integration", "Python/Go" or "Go-based"."""
    version = a_version(skills=[a_skill_row("Go"), a_skill_row("C#"), a_skill_row("CI")])
    subjects, _ = rules.resolve_technology_subjects(requirement, ProfileIndex(version))
    assert token in subjects, subjects


@pytest.mark.parametrize(
    "requirement",
    [
        "5+ years of enterprise software development experience",
        "5+ years of senior software development experience",
        "5+ years of large scale software development",
    ],
)
def test_a_modifier_in_the_field_does_not_confirm_it_on_its_own(requirement):
    """A sales role that "grew enterprise accounts" is not enterprise software
    development; a "Senior" title is not senior software work."""
    version = a_version(
        experiences=[
            an_experience_row(
                job_title="Senior Business Development Manager",
                description="Grew enterprise accounts at large scale across the region.",
                start=date(2012, 1, 1),
                end=None,
            )
        ]
    )
    assert _experience(version, requirement, 5).status == STATUS_UNCERTAIN


def test_front_end_is_confirmed_whole_not_by_end_alone():
    tester = a_version(
        experiences=[
            an_experience_row(
                job_title="QA Analyst",
                description="Wrote end-to-end tests for the billing platform.",
                start=date(2012, 1, 1),
                end=None,
            )
        ]
    )
    frontend = a_version(
        experiences=[
            an_experience_row(job_title="Frontend Engineer", description="Built dashboards.", start=date(2012, 1, 1), end=None)
        ]
    )
    requirement = "5+ years of front end development experience"
    assert _experience(tester, requirement, 5).status == STATUS_UNCERTAIN
    assert _experience(frontend, requirement, 5).status == STATUS_MATCHED


@pytest.mark.parametrize(
    "title, description, expected",
    [
        ("Head Chef", "Ran the kitchen and kept it spotless.", STATUS_UNCERTAIN),
        ("IT Support Analyst", "Resolved desktop tickets.", STATUS_MATCHED),
        ("Systems Administrator", "Ran the information technology helpdesk.", STATUS_MATCHED),
    ],
)
def test_it_experience_is_information_technology_not_the_pronoun(title, description, expected):
    version = a_version(
        experiences=[an_experience_row(job_title=title, description=description, start=date(2012, 1, 1), end=None)]
    )
    assert _experience(version, "5+ years of IT experience", 5).status == expected


def test_a_long_combined_explanation_leads_with_the_technology_that_decided_it():
    """The column holds 500 characters; the missing technology must survive the cut."""
    version = a_version(
        experiences=[
            an_experience_row(
                description="Ran Kubernetes, Prometheus, Terraform and Ansible for the platform.",
                start=date(2012, 1, 1),
                end=None,
            )
        ]
    )
    judgment = _experience(
        version, "5+ years with Kubernetes, Prometheus, Terraform, Ansible and Go", 5
    )
    assert judgment.status == STATUS_MISSING
    assert " go" in judgment.explanation[:500].lower()


# --- second review round (2026-09-26) -----------------------------------------
#
# Each of these was found by an adversarial review of matching_v5_1 itself and
# reproduced offline before it was fixed.


def _skills_only(*names):
    return a_version(skills=[a_skill_row(name) for name in names])


def test_a_slash_inside_the_last_item_of_a_comma_list_is_not_an_or():
    """"React, TypeScript, HTML/CSS" asks for all of them; a React-only profile
    was MATCHED because the slash in HTML/CSS was read as the list's "or"."""
    version = _skills_only("React", "HTML", "CSS")
    judgment = _experience(version, "Experience with React, TypeScript, HTML/CSS", None)
    assert judgment.status != STATUS_MATCHED


@pytest.mark.parametrize(
    "requirement",
    ["Python and/or Go", "Python (or Go)", "Experience with Go or Python"],
)
def test_and_or_and_parenthesised_or_offer_alternatives(requirement):
    assert _experience(_skills_only("Python"), requirement, None).status == STATUS_MATCHED


def test_ruby_on_rails_is_one_technology_not_three():
    subjects, any_of = rules.resolve_technology_subjects(
        "3+ years of Ruby on Rails or Django", ProfileIndex(_skills_only("Django"))
    )
    assert subjects == ["ruby on rails", "django"] and any_of is True


def test_an_or_before_a_dotted_name_is_still_an_or():
    version = a_version(
        experiences=[an_experience_row(description="Built Java services.", start=date(2015, 1, 1), end=None)]
    )
    assert _experience(version, "5+ years of Java or .NET", 5).status == STATUS_MATCHED


def test_a_lowercase_short_technology_with_a_digit_still_resolves():
    """k6 is written in lower case by its own project; dropping it judged
    "k6 and JMeter" on JMeter alone."""
    subjects, _ = rules.resolve_technology_subjects(
        "3+ years with k6 and JMeter", ProfileIndex(_skills_only("JMeter"))
    )
    assert "k6" in subjects


@pytest.mark.parametrize(
    "requirement",
    ["R&D experience", "C-level stakeholder management experience"],
)
def test_the_short_subject_guard_also_covers_requirements_without_a_duration(requirement):
    version = _skills_only("R", "C", "Go")
    assert _experience(version, requirement, None).status != STATUS_MATCHED


@pytest.mark.parametrize(
    "title, description, requirement, expected",
    [
        # "end" and "development" both need a qualifier; neither confirms alone
        ("Head Chef", "Owned end-to-end development of seasonal menus.",
         "5+ years of front end development experience", STATUS_UNCERTAIN),
        # the posting's own field is a qualifier-dependent word: a role naming it confirms
        ("Engineering Manager", "Ran the platform organisation.",
         "5+ years of engineering experience", STATUS_MATCHED),
        # IT narrows the field by itself
        ("IT Systems Administrator", "Ran the servers.",
         "5+ years of IT systems experience", STATUS_MATCHED),
        # a closed compound is the two-word field
        ("Senior Frontend Developer", "Built React applications.",
         "5+ years of front end development experience", STATUS_MATCHED),
    ],
)
def test_field_confirmation_after_the_second_review(title, description, requirement, expected):
    version = a_version(
        experiences=[an_experience_row(job_title=title, description=description, start=date(2012, 1, 1), end=None)]
    )
    assert _experience(version, requirement, 5).status == expected


def test_a_degree_that_starts_after_as_of_is_in_progress():
    """The resume structurer stores "Expected May 2027" as a start date alone."""
    version = a_version(
        education=[
            Education(id=902, institution="Uni", degree="BSc", field_of_study="Computer Science",
                      start_date=date(2027, 5, 1), end_date=None)
        ]
    )
    judgment = rules.judge_education(
        description="Bachelor's degree in Computer Science", degree_level_required="bachelor",
        field_required=None, index=0, is_required=True, profile=ProfileIndex(version), as_of=AS_OF,
    )
    assert judgment.status == STATUS_UNCERTAIN


def test_a_denial_inside_a_clause_about_something_else_is_not_a_denial_of_the_work():
    version = a_version(
        experiences=[
            an_experience_row(
                description="Designed stateless Go services that do not use sticky sessions.",
                start=date(2015, 1, 1), end=None,
            )
        ]
    )
    assert _experience(version, "3+ years of Go", 3).status == STATUS_MATCHED


def test_an_any_of_match_cites_only_the_technology_that_matched():
    """The others fell short; under a MATCHED verdict every cited item is
    stored as qualifying, so citing them showed a shortfall as support."""
    version = a_version(
        experiences=[
            an_experience_row(description="Built Go services.", start=date(2012, 1, 1), end=None),
            an_experience_row(description="Ran a Kubernetes cluster.", start=date(2025, 1, 1), end=None),
        ]
    )
    judgment = _experience(version, "3+ years of Kubernetes or Go", 3)
    assert judgment.status == STATUS_MATCHED
    assert judgment.evidence and all("Kubernetes" not in (item.quote or "") for item in judgment.evidence)


def test_an_any_of_uncertain_leads_with_the_technology_that_kept_it_off_missing():
    version = _skills_only("Go")
    judgment = _experience(version, "5+ years of Java, Kotlin, Scala, Clojure or Go", 5)
    assert judgment.status == STATUS_UNCERTAIN
    assert " go" in judgment.explanation[:500].lower()
