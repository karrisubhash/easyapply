"""Two real postings that the deterministic structurer read as empty.

Both were pasted into the web app, both processing runs "completed", and both
stored a `job_requirements` row with every list empty. Matching then had
nothing to judge: every profile version scored the same, and the Match page
said the posting "states no required requirements" beside a job whose text
plainly lists them. The defect is upstream of the matcher, in two different
places:

  * R1 — a question-form heading. "What are we looking for?" matched none of
    the required headings: they spell the phrase "what we are looking for" and
    allow a trailing colon, never a question mark. Nothing before the first
    recognised heading is read, so the whole requirements list was skipped.
    Its partner "What you will be doing?" has to be recognised in the same
    change: once the requirements section opens, an unrecognised duties
    heading would leave every duty filed as a REQUIRED item.

  * R2 — headings glued to their first sentence. A posting copied out of a
    job board arrives as "Mandatory Technical SkillsThe candidate must have
    ..." — the heading's line break lost, the heading and the paragraph on one
    line. No line is a heading, so again nothing is read. The heading's own
    hyphen is U+2011 ("Good‑to‑Have"), which the preferred-heading pattern did
    not accept either.

The postings are the users' own data, reproduced verbatim. Expectations were
written from the posting text and the structurer's technology vocabulary
before the fix was run against them.

No database: the deterministic structurer is a pure function of the text.
"""

from app.models.job import Job
from app.schemas.job_candidate import JobCandidate
from app.services.job.structuring import DeterministicJobStructurer, verify_job_provenance

#: Job 4 in the development database, "Python Developer · Abc".
QUESTION_HEADINGS_POSTING = """You will leverage your technical expertise to drive the development of our website, property manager tools.

What are we looking for?

2+ years of experience
Excellent in cross-browser web development using Python/Django.
Really good understanding of HTML, CSS, Jquery
Strong database concepts
Experience with DevOps practices and platforms (AWS, Fabric, Apache/Linux etc)
Really good UX/UI sense
Experience with caching technologies, NoSql databases
Leadership skills, ability to handle, mentor junior developers
Prior startup experience is a plus

What you will be doing?

Develop high-volume, low-latency global products
Take ownership of all web based product feature enhancements
Ability to work on multiple projects at a time
Work with product/design team to come up with an awesome product design/workflow
Write well designed, testable, efficient, reusable code.
Investigate and present technology solutions and contribute to architectural improvements
Mentor/Lead junior developers

Skills:- Django, Flask, MySQL, PostgreSQL and NodeJS (Node.js)"""

#: Job 5 in the development database, "Full Stack Developer · TCS". The
#: non-breaking hyphens (U+2011) are the posting's own.
GLUED_HEADINGS_POSTING = (
    "About the job\n"
    "Greetings from TCS!!!\n"
    "\n"
    "We are looking for one of our requirements of Full Stack Developer – React & "
    "PythonDesired Experience Range** 8+yrs yearsLocation - Hyderabad \n"
    "\n"
    "The developer will be responsible for designing, developing, and maintaining backend "
    "services using Python, following clean coding practices and scalable architecture "
    "principles. This includes building and exposing RESTful APIs, handling business logic, "
    "error handling, logging, and performance optimization.The role involves close "
    "collaboration with frontend components developed using React, enabling seamless API "
    "integration and supporting UI workflows required for operational dashboards, monitoring "
    "tools, and automation interfaces.The developer is expected to work closely with DevOps "
    "and platform teams to integrate applications into CI/CD pipelines, participate in code "
    "reviews, adhere to enterprise engineering standards, and support deployments and issue "
    "resolution across environments.\n"
    "\n"
    "\n"
    "Mandatory Technical SkillsThe candidate must have strong hands‑on experience in "
    "Python, including backend application development and REST API implementation using "
    "frameworks such as Flask, FastAPI, or Django.Strong hands‑on experience with React "
    "JS is required, including component development, API integration, state management, and "
    "supporting basic UI workflows.The candidate must have experience working with "
    "Git‑based version control systems and exposure to CI/CD pipelines and Linux "
    "environments.\n"
    "Good‑to‑Have SkillsExperience with cloud platforms (AWS preferred) and "
    "understanding of cloud‑based deployments is desirable. Exposure to Docker and basic "
    "Kubernetes concepts is an added advantage. Experience working in BFSI or other regulated "
    "enterprise environments is preferred.\n"
    "What We Are Looking ForWe are looking for candidates with real delivery experience, not "
    "theoretical exposure. The candidate should have worked on production‑grade "
    "applications or workflows, handled enterprise‑scale systems, and be comfortable "
    "working in a fast‑paced, compliance‑driven environment.\n"
    "This role is not intended for pure frontend developers, pure data engineers, or "
    "candidates without hands‑on Python backend."
)


def structure(description: str) -> JobCandidate:
    job = Job(
        id=1,
        user_id=1,
        source_type="linkedin",
        source_url="https://example.test/jobs/1",
        canonical_url="https://example.test/jobs/1",
        content_hash="0" * 64,
        job_title="Engineer",
        raw_payload={},
        description_text=description,
    )
    return DeterministicJobStructurer().structure(job).candidate


def required_tech(candidate: JobCandidate) -> set[str]:
    return {t.name for t in candidate.technologies if t.is_required}


def preferred_tech(candidate: JobCandidate) -> set[str]:
    return {t.name for t in candidate.technologies if not t.is_required}


def every_requirement_quote(candidate: JobCandidate) -> list[str]:
    """Every quote cited by something the matcher will judge — responsibilities
    excluded, because the matcher never judges them."""
    items = [
        *candidate.required_skills,
        *candidate.preferred_skills,
        *candidate.required_experience,
        *candidate.preferred_experience,
        *candidate.education,
        *candidate.certifications,
        *candidate.technologies,
        *candidate.qualifications,
    ]
    return [cite.quote for item in items for cite in item.provenance]


def judged_count(candidate: JobCandidate) -> int:
    return len(every_requirement_quote(candidate))


# =============================================================================
# R1 — "What are we looking for?"
# =============================================================================


def test_a_question_form_requirements_heading_is_read():
    """The defect: this posting produced an empty reading."""
    candidate = structure(QUESTION_HEADINGS_POSTING)

    assert judged_count(candidate) > 0
    assert {"Python", "Django", "AWS", "Linux"} <= required_tech(candidate)
    assert [item.minimum_years for item in candidate.required_experience] == [2.0]


def test_the_question_form_duties_heading_closes_the_requirements():
    """The duties are responsibilities, never requirements. Without the duties
    heading recognised, every line under it would be a REQUIRED item."""
    candidate = structure(QUESTION_HEADINGS_POSTING)

    quotes = every_requirement_quote(candidate)
    for duty in (
        "Develop high-volume, low-latency global products",
        "Take ownership of all web based product feature enhancements",
        "Mentor/Lead junior developers",
    ):
        assert duty not in quotes
    assert "Develop high-volume, low-latency global products" in [
        item.description for item in candidate.responsibilities
    ]


def test_neither_question_heading_is_itself_filed_as_an_item():
    candidate = structure(QUESTION_HEADINGS_POSTING)

    quotes = every_requirement_quote(candidate) + [
        cite.quote for item in candidate.responsibilities for cite in item.provenance
    ]
    assert "What are we looking for?" not in quotes
    assert "What you will be doing?" not in quotes


def test_the_intro_before_the_first_heading_is_still_skipped():
    candidate = structure(QUESTION_HEADINGS_POSTING)

    assert not any("leverage your technical expertise" in q for q in every_requirement_quote(candidate))


# =============================================================================
# R2 — headings glued to their first sentence
# =============================================================================


def test_a_glued_mandatory_skills_heading_is_read_as_required():
    """The defect: this posting produced an empty reading."""
    candidate = structure(GLUED_HEADINGS_POSTING)

    assert {"Python", "Flask", "FastAPI", "Django", "React", "Git", "Linux"} <= required_tech(
        candidate
    )


def test_a_glued_good_to_have_heading_with_non_breaking_hyphens_is_preferred():
    candidate = structure(GLUED_HEADINGS_POSTING)

    assert {"AWS", "Docker", "Kubernetes"} <= preferred_tech(candidate)
    assert not {"AWS", "Docker", "Kubernetes"} & required_tech(candidate)


def test_a_glued_heading_is_not_part_of_the_quote():
    """The quote is the text after the heading, verbatim — so it still verifies
    against the description, and it does not carry the heading's words."""
    candidate = structure(GLUED_HEADINGS_POSTING)

    quotes = every_requirement_quote(candidate)
    assert quotes
    assert not any(q.startswith("Mandatory Technical Skills") for q in quotes)
    assert not any(q.startswith("Good‑to‑Have Skills") for q in quotes)
    assert any(q.startswith("The candidate must have strong hands") for q in quotes)


def test_glued_heading_quotes_verify_against_the_description():
    candidate = structure(GLUED_HEADINGS_POSTING)

    verified = verify_job_provenance(candidate, GLUED_HEADINGS_POSTING)
    for item in [*verified.technologies, *verified.qualifications]:
        assert all(cite.verified for cite in item.provenance), item


def test_the_duties_paragraph_before_the_first_glued_heading_is_skipped():
    """"The developer will be responsible for ..." sits before any heading.
    React and CI/CD named there are not requirements until a heading says so."""
    candidate = structure(GLUED_HEADINGS_POSTING)

    assert not any(q.startswith("The developer will be responsible") for q in every_requirement_quote(candidate))


# =============================================================================
# Required vs preferred, by the line's own words
# =============================================================================


def test_a_line_that_calls_itself_a_plus_is_preferred_even_under_requirements():
    """"Prior startup experience is a plus" sat under "What are we looking
    for?" and was filed as a REQUIRED skill."""
    candidate = structure(QUESTION_HEADINGS_POSTING)

    assert "Prior startup experience is a plus" in [s.name for s in candidate.preferred_skills]
    assert "Prior startup experience is a plus" not in [s.name for s in candidate.required_skills]


def test_inline_preference_wording_only_ever_demotes():
    candidate = structure(
        "Nice to have:\n"
        "- Kubernetes is required\n"
        "Requirements:\n"
        "- Docker would be a big plus\n"
        "- Terraform is an added advantage.\n"
        "- Python (a plus if you also know Django)\n"
    )

    assert "Kubernetes" in {t.name for t in candidate.technologies if not t.is_required}
    assert {"Docker", "Terraform"} <= {t.name for t in candidate.technologies if not t.is_required}
    # Not the line's closing verdict, so it stays required.
    assert {"Python", "Django"} <= {t.name for t in candidate.technologies if t.is_required}


# =============================================================================
# The glued-heading split stays narrow
# =============================================================================


def test_an_ordinary_camel_cased_line_is_not_split():
    """"JavaScript" has a camel-case seam ("Java|Script"), and the text before
    it must not be read as a heading. It must stay one requirement line."""
    candidate = structure("Requirements:\n- Working with JavaScript and TypeScript\n- Python\n")

    assert {"JavaScript", "TypeScript", "Python"} <= required_tech(candidate)


def test_a_sentence_starting_with_heading_words_is_not_split():
    candidate = structure("Requirements:\nQualifications matter here: Python and Django\n")

    assert {"Python", "Django"} <= required_tech(candidate)


def test_a_glued_heading_before_any_requirements_opens_its_section():
    candidate = structure("RequirementsThe candidate must know Python.\nNice to haveExposure to Docker.\n")

    assert required_tech(candidate) == {"Python"}
    assert preferred_tech(candidate) == {"Docker"}
