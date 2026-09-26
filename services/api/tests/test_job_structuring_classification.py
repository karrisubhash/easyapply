"""What a requirement line is filed as, once its section is known.

Four audited misclassifications, each of which the matcher turned into a
confident wrong verdict:

  * J3 — location, workplace, eligibility and engagement lines ("Remote (US
    only)", "US citizenship required", "6-month contract") fell through to the
    catch-all and became required SKILLS, answered MISSING.
  * J4 — "Must be at least 18 years of age" became eighteen years of required
    experience.
  * J5 — degree letters without a degree: "Certified Scrum Master" -> master,
    "AWS Certified Developer - Associate" -> associate, "sub-10 ms" -> master.
  * J6 — technology aliases that are English: "TS/SCI" -> TypeScript (and the
    clearance lost), "the rest of the team" -> REST.

No database, except for the one test that asks the real matcher what it makes
of the corrected reading.
"""

import pytest

from app.models.job import Job
from app.schemas.job_candidate import JobCandidate
from app.services.job.structuring import DeterministicJobStructurer


def structure(description: str) -> JobCandidate:
    job = Job(
        id=1,
        user_id=1,
        source_type="other",
        source_url="https://example.test/jobs/1",
        canonical_url="https://example.test/jobs/1",
        content_hash="0" * 64,
        job_title="Engineer",
        raw_payload={},
        description_text=description,
    )
    return DeterministicJobStructurer().structure(job).candidate


def required_line(line: str) -> JobCandidate:
    return structure(f"Requirements:\n- {line}\n")


def skill_names(candidate: JobCandidate) -> list[str]:
    return [s.name for s in candidate.required_skills + candidate.preferred_skills]


def qualification_texts(candidate: JobCandidate) -> list[str]:
    return [q.description for q in candidate.qualifications]


def tech_names(candidate: JobCandidate) -> set[str]:
    return {t.name for t in candidate.technologies}


# =============================================================================
# J3 — conditions of employment are qualifications, never skills
# =============================================================================


@pytest.mark.parametrize(
    "line",
    [
        # the six the audit found
        "On-site in Munich 3 days a week",
        "Remote (US only)",
        "Right to work in the UK",
        "Visa sponsorship is not available",
        "US citizenship required",
        "6-month contract",
        # the same classes, other phrasings
        "Onsite 3 days a week",
        "On site at our London office",
        "Hybrid (3 days in office)",
        "Hybrid, 2 days per week in Dublin",
        "In-office 4 days a week",
        "Remote only",
        "Legally authorized to work in the US",
        "Work permit for Switzerland",
        "We are unable to sponsor",
        "Green card holders",
        "Eligible for security clearance",
        "Active Secret clearance",
        "Background check required",
        "12 month fixed-term",
        "Contract length: 9 months",
        "W2 only",
        "Travel up to 25%",
        "On-call rotation",
    ],
)
def test_a_condition_of_employment_is_a_qualification_not_a_skill(line):
    candidate = required_line(line)

    assert qualification_texts(candidate) == [line]
    assert skill_names(candidate) == []
    assert candidate.qualifications[0].is_required is True


@pytest.mark.parametrize(
    "line",
    [
        "On-site customer support",
        "Customs clearance",
        "Contract management",
        "Experience with Visa payment APIs",
        "Remote sensing",
        "Hybrid mobile apps",
    ],
)
def test_a_competency_that_shares_a_word_stays_a_skill(line):
    """The negative half. Each of these is a competency statement that opens
    with, or contains, a word the recognisers look for."""
    candidate = required_line(line)

    assert qualification_texts(candidate) == []
    assert skill_names(candidate) == [line]


def test_contract_management_experience_is_experience_not_an_engagement_term():
    candidate = required_line("5 years contract management experience")

    assert [e.minimum_years for e in candidate.required_experience] == [5.0]
    assert qualification_texts(candidate) == []


def test_a_preferred_condition_keeps_its_section_flag():
    candidate = structure("Preferred:\n- Remote (EU only)\n")

    assert qualification_texts(candidate) == ["Remote (EU only)"]
    assert candidate.qualifications[0].is_required is False


def test_the_location_lead_itself_is_unchanged_and_still_in_step_with_matching():
    """The new recognisers are separate on purpose: `_LOCATION_LEAD` is held
    identical to `matching.geography.LOCATION_LEAD`."""
    from app.services.job.structuring import _LOCATION_LEAD
    from app.services.matching import geography

    assert _LOCATION_LEAD.pattern == geography.LOCATION_LEAD.pattern


def test_the_matcher_answers_an_eligibility_line_uncertain_not_missing():
    """The harm, end to end: "US citizenship required" used to be a required
    skill the matcher reported MISSING."""
    from datetime import date

    from app.services.matching.engine import evaluate
    from tests.matching_fixtures import AS_OF, a_skill_row, a_version, an_experience_row

    candidate = structure("Requirements:\n- Python\n- US citizenship required\n- 6-month contract\n")
    version = a_version(
        skills=[a_skill_row("Python")],
        experiences=[
            an_experience_row(
                job_title="Backend Engineer",
                start=date(2021, 3, 1),
                end=None,
                description="Python services.",
            )
        ],
    )
    evaluation = evaluate(candidate, version, as_of=AS_OF)
    verdicts = {j.requirement_label: j for j in evaluation.judgments}

    for label in ("US citizenship required", "6-month contract"):
        assert verdicts[label].category == "qualification"
        assert verdicts[label].status == "uncertain"
    assert evaluation.score.required.missing == 0


# =============================================================================
# J4 — an age is not a duration of experience
# =============================================================================


@pytest.mark.parametrize(
    "line",
    [
        "Must be at least 18 years of age",
        "18+ years of age",
        "Applicants must be 21 years old",
        "Candidates 18 years or older",
        "Must be 18 or older",
    ],
)
def test_an_age_requirement_is_not_experience(line):
    candidate = required_line(line)

    assert candidate.required_experience == []
    assert qualification_texts(candidate) == [line]
    assert skill_names(candidate) == []


def test_real_years_on_the_same_line_as_an_age_are_still_read():
    candidate = required_line("Must be 18 years of age with 3+ years of experience")

    assert [e.minimum_years for e in candidate.required_experience] == [3.0]
    assert qualification_texts(candidate) == ["Must be 18 years of age with 3+ years of experience"]


@pytest.mark.parametrize(
    ("line", "minimum", "maximum"),
    [
        ("5+ years of experience", 5.0, None),
        ("2-4 years of experience", 2.0, 4.0),
        ("3 years experience with SQL", 3.0, None),
        ("1 year of experience", 1.0, None),
        ("5 years or more of experience", 5.0, None),
    ],
)
def test_ordinary_year_requirements_are_unaffected(line, minimum, maximum):
    candidate = required_line(line)

    assert [(e.minimum_years, e.maximum_years) for e in candidate.required_experience] == [
        (minimum, maximum)
    ]


# =============================================================================
# J5 — a degree needs degree context
# =============================================================================


@pytest.mark.parametrize(
    "line",
    [
        "Certified Scrum Master",
        "AWS Certified Developer - Associate",
        "Experience with low-latency systems (sub-10 ms)",
        "Latency under 10 ms in production",
        "MS Office",
        "MS SQL Server",
        "Associate Product Manager experience",
        "Master data management",
        "Experience as a BA in fintech",
        "Based in Boston, MA in the Seaport",
    ],
)
def test_degree_letters_without_a_degree_are_not_education(line):
    candidate = required_line(line)

    assert candidate.education == [], line


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("MS in Computer Science", "master"),
        ("M.S. in Computer Science", "master"),
        ("MSc Data Science", "master"),
        ("Master of Science in Computer Science", "master"),
        ("Masters degree in Physics", "master"),
        ("Master degree in engineering", "master"),
        ("MBA preferred", "master"),
        ("BS/MS in Computer Science", "master"),
        ("BSc Computer Science", "bachelor"),
        ("BS in Computer Science", "bachelor"),
        ("B.A. in Economics", "bachelor"),
        ("BA degree", "bachelor"),
        ("Bachelors in Mathematics", "bachelor"),
        ("Associate degree in IT", "associate"),
        ("Associate of Applied Science", "associate"),
        ("Ph.D. in Physics", "doctorate"),
        ("Doctoral degree in Statistics", "doctorate"),
    ],
)
def test_degrees_with_context_are_recognised(line, level):
    candidate = required_line(line)

    assert [e.degree_level for e in candidate.education] == [level], line


def test_a_certification_named_master_is_still_a_certification():
    candidate = required_line("Certified Scrum Master")

    assert [c.name for c in candidate.certifications] == ["Certified Scrum Master"]


# =============================================================================
# J6 — technology aliases that are also English
# =============================================================================


def test_a_ts_sci_clearance_is_a_qualification_and_not_typescript():
    """Both halves of the defect: the phantom technology, and the clearance
    that was lost because the phantom 'matched something'."""
    candidate = required_line("Active TS/SCI clearance")

    assert tech_names(candidate) == set()
    assert qualification_texts(candidate) == ["Active TS/SCI clearance"]


def test_a_clearance_line_that_also_names_a_technology_is_filed_as_both():
    candidate = required_line("Secret clearance and strong Python")

    assert qualification_texts(candidate) == ["Secret clearance and strong Python"]
    assert tech_names(candidate) == {"Python"}


@pytest.mark.parametrize(
    ("line", "absent"),
    [
        ("Collaborate with the rest of the engineering team", "REST"),
        ("Ability to react quickly to incidents", "React"),
        ("Graph node traversal", "Node.js"),
        ("Experience with Next.js", "JavaScript"),
        ("Experience with Vue.js", "JavaScript"),
    ],
)
def test_an_english_word_is_not_a_technology(line, absent):
    candidate = required_line(line)

    assert absent not in tech_names(candidate), line


@pytest.mark.parametrize(
    ("line", "present"),
    [
        ("Experience building REST APIs", "REST"),
        ("RESTful services", "REST"),
        ("React and Redux", "React"),
        ("Node and Express", "Node.js"),
        ("Node.js services", "Node.js"),
        ("React.js front ends", "React"),
        ("JS and TypeScript", "JavaScript"),
        ("TypeScript", "TypeScript"),
    ],
)
def test_the_technology_spellings_still_match(line, present):
    candidate = required_line(line)

    assert present in tech_names(candidate), line
