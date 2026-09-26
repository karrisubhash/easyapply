"""Where a section ends, and which headings open one.

Two defects, both of which dropped real requirements without any error:

  * J1 — an unbulleted line read as a heading. Scraped `<li>` text carries no
    bullet glyphs, so "Requirements:\\nPython\\nDjango\\n…" had its section
    closed by "Python" and produced nothing at all. An unrecognised
    heading-shaped line now closes a section only when it carries a heading
    mark — see `structuring._closes_section` for the five marks.
  * J2 — common headings not recognised. Amazon's "Basic Qualifications" fell
    through to the unknown-heading rule, so every basic requirement was dropped
    and only "Preferred Qualifications" survived: a posting read as asking for
    its optional items and nothing else.

No database: the deterministic structurer is a pure function of the text.
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


def required_tech(candidate: JobCandidate) -> set[str]:
    return {t.name for t in candidate.technologies if t.is_required}


def preferred_tech(candidate: JobCandidate) -> set[str]:
    return {t.name for t in candidate.technologies if not t.is_required}


def required_skill_names(candidate: JobCandidate) -> list[str]:
    return [s.name for s in candidate.required_skills]


# =============================================================================
# J1 — unbulleted postings
# =============================================================================


def test_an_unbulleted_requirements_list_is_read_not_dropped():
    """The defect at its narrowest: every line used to vanish."""
    candidate = structure(
        "Requirements:\n"
        "Python\n"
        "Django\n"
        "Excellent written communication\n"
        "3+ years Python\n"
    )

    assert required_tech(candidate) == {"Python", "Django"}
    assert required_skill_names(candidate) == ["Excellent written communication"]
    assert [e.minimum_years for e in candidate.required_experience] == [3.0]


def test_unbulleted_required_and_preferred_sections_stay_separate():
    candidate = structure(
        "Requirements:\nPython\nDjango\n\nNice to have:\nKubernetes\nTerraform\n"
    )

    assert required_tech(candidate) == {"Python", "Django"}
    assert preferred_tech(candidate) == {"Kubernetes", "Terraform"}


def test_blank_line_separated_unbulleted_items_are_all_kept():
    """Paragraph-per-item layouts: no item is packed against the next, so the
    block-break mark never fires."""
    candidate = structure("Requirements:\n\nPython\n\nDjango\n\nFlask\n")

    assert required_tech(candidate) == {"Python", "Django", "Flask"}


def test_unbulleted_responsibilities_are_kept_too():
    candidate = structure("Responsibilities:\nBuild APIs\nMentor engineers\n")

    assert [r.description for r in candidate.responsibilities] == ["Build APIs", "Mentor engineers"]


def test_a_bulleted_section_is_read_exactly_as_before():
    """Every heading-shaped line is unbulleted, so inside a bulleted list the
    list-style mark fires on all of them: the old rule, unchanged."""
    candidate = structure("Requirements:\n- Python\nTooling We Love\n- Java\n")

    assert required_tech(candidate) == {"Python"}
    assert "Java" not in {t.name for t in candidate.technologies}


def test_mixed_unbulleted_then_bulleted_sections():
    candidate = structure(
        "Requirements:\nPython\nDjango\n\nPreferred:\n- Experience with Kubernetes\n- Redis\n"
    )

    assert required_tech(candidate) == {"Python", "Django"}
    assert preferred_tech(candidate) == {"Kubernetes", "Redis"}


def test_mixed_bulleted_then_unbulleted_sections():
    """List style is a property of each section, fixed by its own first item."""
    candidate = structure(
        "Requirements:\n- Python\n- Django\n\nNice-to-have:\nKubernetes\nRedis\n"
    )

    assert required_tech(candidate) == {"Python", "Django"}
    assert preferred_tech(candidate) == {"Kubernetes", "Redis"}


# --- the heading marks ---------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "Tech we use:",  # 1. trailing colon
        "## Tooling",  # 2. Markdown heading
        "TOOLS OF THE TRADE",  # 3. set in capitals
    ],
)
def test_a_marked_unknown_heading_closes_an_unbulleted_section(heading):
    """The lines under an unknown heading are dropped rather than filed as
    REQUIRED — the direction that cannot misstate what the posting asks for."""
    candidate = structure(f"Requirements:\nPython\nDjango\n{heading}\nKubernetes\n")

    assert required_tech(candidate) == {"Python", "Django"}
    assert "Kubernetes" not in {t.name for t in candidate.technologies}


def test_an_unbulleted_line_introducing_a_bulleted_list_is_its_heading():
    """Mark 4, look-ahead half: a change of list style."""
    candidate = structure("Requirements:\nPython\nTooling\n- Kafka\n")

    assert required_tech(candidate) == {"Python"}
    assert "Kafka" not in {t.name for t in candidate.technologies}


def test_a_block_break_in_a_packed_list_opens_a_new_block():
    """Mark 5: two packed items establish the layout, so a heading-shaped line
    after a blank line is the next block's heading."""
    candidate = structure("Requirements:\nPython\nDjango\n\nTooling\nKafka\n")

    assert required_tech(candidate) == {"Python", "Django"}
    assert "Kafka" not in {t.name for t in candidate.technologies}


@pytest.mark.parametrize("item", ["CI/CD", "AWS GCP", "CISSP", "SQL"])
def test_capitalised_items_are_not_mistaken_for_shouted_headings(item):
    """Acronyms, slash-joined acronyms and technology pairs are items."""
    candidate = structure(f"Requirements:\nPython\n{item}\nDjango\n")

    assert {"Python", "Django"} <= required_tech(candidate)


@pytest.mark.parametrize(
    "heading",
    ["About Acme", "Benefits", "Perks and benefits", "What we offer", "Who we are", "Our culture",
     "Tech stack", "Equal Opportunity Employer", "How to apply", "Why join us"],
)
def test_a_known_non_requirement_heading_closes_an_unbulleted_section(heading):
    """Without a mark, these would now be items — `_OTHER_HEADINGS` is what
    keeps "Benefits" and the lines under it out of the requirements."""
    candidate = structure(f"Requirements:\nPython\nDjango\n{heading}\nKafka\nFree lunch\n")

    assert required_tech(candidate) == {"Python", "Django"}
    assert required_skill_names(candidate) == []


def test_about_you_is_a_requirements_heading_not_a_company_one():
    candidate = structure("About Acme\nWe build things.\nAbout you\nPython\nDjango\n")

    assert required_tech(candidate) == {"Python", "Django"}


def test_a_decimal_year_on_an_unbulleted_line_is_not_a_numbered_bullet():
    """"3.5 years" used to lose "3." to the numbered-bullet prefix and read as
    5 years — harmless while unbulleted sections were dropped, not after."""
    candidate = structure("Requirements:\n3.5 years of experience with Python\n")

    assert candidate.required_experience[0].minimum_years == 3.5
    assert candidate.required_experience[0].description == "3.5 years of experience with Python"


def test_a_numbered_list_is_still_stripped():
    candidate = structure("Requirements:\n1. Experience with Python\n2) Experience with Django\n")

    assert [t.provenance[0].quote for t in candidate.technologies] == [
        "Experience with Python",
        "Experience with Django",
    ]


@pytest.mark.parametrize("glyph", ["◦", "○", "■", "►", "▸", "➢", "✓", "✔", "–", "—"])
def test_other_bullet_glyphs_are_list_markers(glyph):
    candidate = structure(f"Requirements:\n{glyph} Strong communication skills\n")

    assert required_skill_names(candidate) == ["Strong communication skills"]


def test_text_before_the_first_heading_is_still_skipped():
    """Unbulleted preamble never becomes a requirement."""
    candidate = structure("Python\nDjango\nRequirements:\nJava\n")

    assert {t.name for t in candidate.technologies} == {"Java"}


def test_every_unbulleted_item_quotes_its_own_line():
    description = "Requirements:\nPython\nDjango\n"
    candidate = structure(description)

    assert [t.provenance[0].quote for t in candidate.technologies] == ["Python", "Django"]
    assert all(t.provenance[0].source_section == "required" for t in candidate.technologies)


# =============================================================================
# J2 — heading vocabulary
# =============================================================================


@pytest.mark.parametrize(
    "heading",
    [
        "Basic Qualifications",
        "Basic Qualifications:",
        "Required Skills:",
        "Required skill",
        "Minimum requirements",
        "Key Qualifications",
        "Essential skills",
        "Essential",
        "Must-haves:",
        "Must haves",
        "Must-have",
        "What you'll bring:",
        "What you bring",
        "About you",
        "Your profile",
        "Skills required",
        "Qualifications and experience",
        "Requirements & Qualifications",
    ],
)
def test_required_heading_variants(heading):
    candidate = structure(f"{heading}\n- Experience with Python\n")

    assert required_tech(candidate) == {"Python"}, heading


@pytest.mark.parametrize(
    "heading",
    [
        "Nice to haves:",
        "Nice-to-have:",
        "Nice-to-haves",
        "Good to have",
        "Desired",
        "Desired skills:",
        "Desirable",
        "Bonus points",
        "Bonus points:",
        "Bonus if you have:",
        "Preferred skills",
        "Preferred Qualifications",
    ],
)
def test_preferred_heading_variants(heading):
    candidate = structure(f"{heading}\n- Experience with Python\n")

    assert preferred_tech(candidate) == {"Python"}, heading


def test_bare_bonus_is_not_a_preferred_heading():
    """"Bonus" heads compensation lines as often as optional skills; read as a
    preferred heading it would turn "Equity" into a preferred skill."""
    candidate = structure("Compensation\nBase salary\nBonus\nEquity\n")

    assert candidate.preferred_skills == []
    assert candidate.required_skills == []


AMAZON_POSTING = """About the team
We build payments.

Basic Qualifications
- 3+ years of non-internship professional software development experience
- Experience programming with at least one software programming language
- Bachelor's degree in Computer Science

Preferred Qualifications
- Master's degree in computer science
- Experience with AWS
"""


def test_an_amazon_style_posting_keeps_its_basic_qualifications():
    """The inversion itself: only the preferred half used to survive."""
    candidate = structure(AMAZON_POSTING)

    assert [e.minimum_years for e in candidate.required_experience] == [3.0]
    assert required_skill_names(candidate) == [
        "Experience programming with at least one software programming language"
    ]
    levels = {(e.degree_level, e.is_required) for e in candidate.education}
    assert levels == {("bachelor", True), ("master", False)}
    assert preferred_tech(candidate) == {"AWS"}
