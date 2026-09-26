"""The deterministic structurer, with no database.

Deliberately dumb, the same argument `resume.structuring.DeterministicResumeStructurer`
makes for itself: it finds headings, treats the lines under them as items, and
quotes the exact line it used. Every test here holds it to that — nothing
invents a field the text does not contain, and no AI interpretation happens
anywhere in this file.
"""

import pytest

from app.models.job import Job
from app.schemas.job_candidate import JobCandidate, JobProvenance, JobSkillItem
from app.services.job.structuring import (
    DeterministicJobStructurer,
    verify_job_provenance,
)


def make_job(description: str | None) -> Job:
    return Job(
        id=1,
        user_id=1,
        source_type="linkedin",
        source_url="https://x.example/1",
        canonical_url="https://x.example/1",
        content_hash="a" * 64,
        job_title="Test",
        raw_payload={},
        description_text=description,
    )


def structure(description: str | None) -> JobCandidate:
    return DeterministicJobStructurer().structure(make_job(description)).candidate


# --- no description ------------------------------------------------------


def test_no_description_produces_an_empty_candidate_not_a_failure():
    candidate = structure(None)
    for field_name in JobCandidate.model_fields:
        assert getattr(candidate, field_name) == []


def test_an_empty_string_description_also_produces_nothing():
    assert structure("").required_skills == []


def test_text_with_no_recognized_heading_extracts_nothing():
    """No section, no claim — the same discipline the resume structurer
    applies to lines above its first heading."""
    candidate = structure("Just a paragraph about the company with no headings at all.")
    assert all(len(getattr(candidate, f)) == 0 for f in JobCandidate.model_fields)


# --- required vs preferred ----------------------------------------------


def test_required_and_preferred_sections_are_kept_separate():
    description = (
        "Requirements:\n"
        "- Experience with Python\n\n"
        "Preferred:\n"
        "- Experience with Kubernetes\n"
    )
    candidate = structure(description)

    required_tech = {t.name for t in candidate.technologies if t.is_required}
    preferred_tech = {t.name for t in candidate.technologies if not t.is_required}
    assert required_tech == {"Python"}
    assert preferred_tech == {"Kubernetes"}


def test_a_line_before_any_heading_is_not_absorbed():
    description = "- Experience with Python\nRequirements:\n- Experience with Java\n"
    candidate = structure(description)
    names = {t.name for t in candidate.technologies}
    assert names == {"Java"}


def test_recognized_heading_variants_all_route_correctly():
    for heading in ("Requirements:", "Must Have", "Minimum Qualifications:", "What You Need"):
        candidate = structure(f"{heading}\n- Experience with Python\n")
        assert any(t.is_required for t in candidate.technologies), heading

    for heading in ("Preferred:", "Nice to Have", "Bonus Points"):
        candidate = structure(f"{heading}\n- Experience with Python\n")
        assert any(not t.is_required for t in candidate.technologies), heading


# --- experience ------------------------------------------------------------


def test_a_plus_years_pattern_is_extracted_as_a_minimum():
    candidate = structure("Requirements:\n- 5+ years of experience in backend development\n")
    assert len(candidate.required_experience) == 1
    item = candidate.required_experience[0]
    assert item.minimum_years == 5.0
    assert item.maximum_years is None


def test_a_year_range_is_extracted_as_both_bounds():
    candidate = structure("Preferred:\n- 2-4 years of experience with GraphQL\n")
    item = candidate.preferred_experience[0]
    assert item.minimum_years == 2.0
    assert item.maximum_years == 4.0


def test_a_bare_years_mention_is_extracted():
    candidate = structure("Requirements:\n- 3 years experience with SQL\n")
    assert candidate.required_experience[0].minimum_years == 3.0


def test_a_decimal_years_mention_is_extracted_whole_not_just_its_fraction():
    """`\\d+` alone treats the decimal point as a separator: "3.5 years" has no
    "-"/"to" for the range branch and no "+" for the plus branch, so the old
    pattern fell through to the bare-years branch and matched only the digits
    after the point — reading a 3.5-year requirement as a 5-year one. Wrong in
    the direction that costs a candidate a match, and confidently wrong rather
    than merely missing, which is the one thing this structurer promises not to
    be (see the module docstring's rule 7)."""
    candidate = structure("Requirements:\n- 3.5 years of experience with Python\n")
    assert candidate.required_experience[0].minimum_years == 3.5


def test_a_decimal_year_range_is_extracted_as_both_bounds():
    candidate = structure("Preferred:\n- 2.5-5.5 years of experience with GraphQL\n")
    item = candidate.preferred_experience[0]
    assert item.minimum_years == 2.5
    assert item.maximum_years == 5.5


# --- education ---------------------------------------------------------


def test_degree_levels_are_recognized():
    for phrase, level in [
        ("Bachelor's degree in Computer Science", "bachelor"),
        ("B.S. in a related field", "bachelor"),
        ("Master's degree preferred", "master"),
        ("PhD in Machine Learning", "doctorate"),
        ("Associate's degree", "associate"),
    ]:
        candidate = structure(f"Requirements:\n- {phrase}\n")
        assert candidate.education, phrase
        assert candidate.education[0].degree_level == level, phrase


def test_education_carries_the_required_flag_from_its_section():
    candidate = structure("Requirements:\n- Bachelor's degree required\n\nPreferred:\n- Master's degree\n")
    required = [e for e in candidate.education if e.is_required]
    preferred = [e for e in candidate.education if not e.is_required]
    assert required and required[0].degree_level == "bachelor"
    assert preferred and preferred[0].degree_level == "master"


# --- certifications ------------------------------------------------------


def test_certification_language_is_recognized():
    candidate = structure("Preferred:\n- AWS Certified Solutions Architect\n")
    assert len(candidate.certifications) == 1
    assert candidate.certifications[0].is_required is False


# --- technologies ----------------------------------------------------------


def test_known_technologies_are_canonicalized():
    candidate = structure("Requirements:\n- Experience with postgres, js, and k8s\n")
    names = {t.name for t in candidate.technologies}
    assert names == {"PostgreSQL", "JavaScript", "Kubernetes"}


def test_multiple_technologies_on_one_line_are_all_captured():
    candidate = structure("Requirements:\n- Python and Django and PostgreSQL\n")
    names = {t.name for t in candidate.technologies}
    assert {"Python", "Django", "PostgreSQL"} <= names


def test_a_line_can_be_classified_under_more_than_one_category():
    """A line naming both a degree and a technology is genuinely both claims —
    filing it under only one would silently drop the other."""
    candidate = structure("Requirements:\n- Bachelor's degree and experience with Python\n")
    assert candidate.education
    assert any(t.name == "Python" for t in candidate.technologies)


# --- responsibilities (no required/preferred axis) --------------------------


def test_responsibilities_carry_no_required_flag():
    candidate = structure("Responsibilities:\n- Design and build scalable APIs\n")
    assert len(candidate.responsibilities) == 1
    assert not hasattr(candidate.responsibilities[0], "is_required")


def test_responsibility_heading_variants():
    for heading in ("Responsibilities:", "What You'll Do", "The Role"):
        candidate = structure(f"{heading}\n- Do the work\n")
        assert candidate.responsibilities, heading


# --- the catch-all: qualifications -------------------------------------------


def test_an_unclassifiable_requirement_becomes_a_qualification_not_dropped():
    candidate = structure("Requirements:\n- Must be authorized to work in the United States\n")
    assert len(candidate.qualifications) == 1
    assert candidate.qualifications[0].is_required is True


def test_a_short_unclassifiable_line_becomes_a_skill_instead():
    candidate = structure("Requirements:\n- Strong communication skills\n")
    assert any(s.name == "Strong communication skills" for s in candidate.required_skills)


# --- provenance ------------------------------------------------------------


def test_every_item_cites_the_exact_line_it_came_from():
    description = "Requirements:\n- Experience with Python\n"
    candidate = structure(description)
    item = candidate.technologies[0]
    assert len(item.provenance) == 1
    assert item.provenance[0].quote == "Experience with Python"
    assert item.provenance[0].source_section == "required"


def test_verify_job_provenance_confirms_a_real_quote():
    description = "Requirements:\n- Experience with Python\n"
    candidate = structure(description)
    verify_job_provenance(candidate, description)
    assert candidate.technologies[0].provenance[0].verified is True


def test_verify_job_provenance_catches_a_fabricated_quote():
    """Meaningless for the deterministic structurer, which can only quote text
    it read — but required infrastructure ahead of whatever structures a job
    next, exactly resume's own justification for verifying its own output."""
    fake = JobCandidate(
        required_skills=[
            JobSkillItem(
                name="Rust",
                provenance=[JobProvenance(quote="Must know Rust", confidence=0.9)],
            )
        ]
    )
    verify_job_provenance(fake, "Requirements: Python only, nothing about Rust")
    assert fake.required_skills[0].provenance[0].verified is False


def test_verify_job_provenance_with_no_description_marks_everything_unverified():
    fake = JobCandidate(
        required_skills=[JobSkillItem(name="X", provenance=[JobProvenance(quote="X", confidence=0.5)])]
    )
    verify_job_provenance(fake, None)
    assert fake.required_skills[0].provenance[0].verified is False


# --- the result envelope --------------------------------------------------


def test_the_structuring_result_names_its_own_structurer_and_schema():
    result = DeterministicJobStructurer().structure(make_job("Requirements:\n- Python\n"))
    assert result.structurer == "deterministic"
    assert result.provider is None
    assert result.model is None
    assert result.schema_version == "1"


# --- the heading that swallowed a whole posting ---------------------------------
#
# Found by driving the real product flow in a browser against the real posting
# from `evaluation/datasets/matching_real_v1`. The job saved, the requirements
# page rendered, matching ran and a resume was tailored — and every required
# requirement had been silently dropped, because the posting is headed
#
#     What we need:
#
# and `_REQUIRED_HEADINGS` knew only "What you need". The line fell through to
# `_UNKNOWN_HEADING`, which closes whatever section is open, so Python, Django,
# PostgreSQL, Docker, the degree and both durations went nowhere. What survived
# was "Nice to have:", which IS recognized — so the posting read as a role
# wanting Kubernetes, Redis and AWS, and nothing else.
#
# Nothing in the pipeline reported a problem. Matching scored a backend engineer
# against three optional technologies and tailoring aimed a resume at them.


REAL_POSTING = """We are looking for a Senior Backend Engineer to join our platform team in Berlin.

What we need:
- Strong Python, with 5+ years of professional software engineering experience
- 3+ years building production services with Django
- Solid PostgreSQL: you should be comfortable designing schemas and reading a query plan
- Comfortable with Docker
- Bachelor's degree in Computer Science or a related field
- Must be legally authorised to work in the EU
- Based in Berlin, or within two hours of CET for our hybrid days

Nice to have:
- Kubernetes
- Redis
- AWS Certified Developer
"""


def test_a_first_person_required_heading_is_not_dropped():
    """The defect itself, at its narrowest."""
    candidate = structure("What we need:\n- Experience with Python\n")

    assert any(t.is_required for t in candidate.technologies)


@pytest.mark.parametrize(
    "heading",
    [
        "What we need:",
        "What We Need",
        "What you'll need:",
        "What we're looking for:",
        "What we are looking for",
        "Who you are:",
        "Experience required:",
        "Skills and experience:",
    ],
)
def test_required_heading_variants_route_to_required(heading):
    """Both persons of each phrasing.

    "What we need" and "What you need" are the same heading written from the two
    ends of the same table. A matcher knowing one and not the other is not making
    a judgement; it is showing which one its author happened to type.
    """
    candidate = structure(f"{heading}\n- Experience with Python\n")

    assert any(t.is_required for t in candidate.technologies), heading


def test_the_real_posting_keeps_every_requirement_it_states():
    """The whole document, asserted the way the browser suite sees it.

    Each of these is something the posting says in so many words, and each was
    absent before the fix. The preferred items are asserted too, because they
    were the ONLY things that survived — a regression that broke the required
    section again while leaving these would otherwise look like a partial pass.
    """
    candidate = structure(REAL_POSTING)

    required = {t.name for t in candidate.technologies if t.is_required}
    preferred = {t.name for t in candidate.technologies if not t.is_required}

    assert {"Python", "Django", "PostgreSQL", "Docker"} <= required
    assert {"Kubernetes", "Redis"} <= preferred

    assert len(candidate.required_experience) == 2
    assert any("Django" in item.description for item in candidate.required_experience)
    assert any("5+ years" in item.description for item in candidate.required_experience)

    assert any("Computer Science" in item.description for item in candidate.education)
    assert any(c.name == "AWS Certified Developer" for c in candidate.certifications)

    # The two the posting states that no resume can evidence. They belong in the
    # candidate — tailoring is what decides they are unanswerable, not the reader.
    assert len(candidate.qualifications) == 2


def test_an_unrecognized_heading_still_closes_its_section():
    """The fix widens recognition; it does not weaken the rule that an unknown
    heading ends a section. Absorbing arbitrary headed prose as requirements
    would misclassify real content, which is the worse failure."""
    candidate = structure(
        "Requirements:\n- Experience with Python\n\nAbout our office:\n- Experience with Java\n"
    )

    names = {t.name for t in candidate.technologies}
    assert "Python" in names
    assert "Java" not in names
