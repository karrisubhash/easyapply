"""Regressions the adversarial review of the job-structuring fixes found.

The unbulleted-section, degree and alias fixes each made one posting shape read
better; these are the shapes they first made read worse, found by probing
realistic postings against the baseline before release.
"""

from __future__ import annotations

import time

import pytest

from app.services.job.structuring import DeterministicJobStructurer, _next_non_empty_lines
from tests.test_job_structuring_sections import (
    preferred_tech,
    required_skill_names,
    required_tech,
    structure,
)


# --- headings that are not requirements, and optional-skill headings ---------------


@pytest.mark.parametrize("heading", ["Pluses", "Bonus", "Additional Skills", "Ideally you also have"])
def test_an_optional_heading_after_unbulleted_requirements_keeps_its_items_optional(heading):
    """Read as an item, the heading became a required skill and every optional
    line under it was promoted to required."""
    candidate = structure(
        "Requirements\n3+ years of Python\nDjango\n\n" f"{heading}\nKubernetes\nTerraform\n"
    )
    assert heading not in required_skill_names(candidate)
    assert {"Kubernetes", "Terraform"} <= preferred_tech(candidate)
    assert not {"Kubernetes", "Terraform"} & required_tech(candidate)


@pytest.mark.parametrize(
    "heading",
    [
        "Why Acme",
        "Company Overview",
        "Role Overview",
        "Life at Acme",
        "Working at Globex",
        # The second review's company, pay, values and terms-of-the-job headings.
        "Pay Transparency",
        "Compensation Range",
        "Base Salary",
        "Benefits Include",
        "Total Rewards",
        "What We Offer You",
        "The Team",
        "Meet the Team",
        "Company Description",
        "Our Commitment to Diversity",
        "Our Core Values",
        "What We Value",
        "Physical Requirements",
        "Job Type",
        "Schedule",
        "Work Environment",
        "About Booking.com",
        "Why you’ll love working here",
    ],
)
def test_a_company_blurb_heading_closes_the_requirements(heading):
    """No blank line before the heading, and no bullets: no heading MARK fires
    (`_closes_section`), so only the heading vocabulary can close the section.
    The first version of this test put a blank line there, which closed the
    section by itself and so passed with the vocabulary removed."""
    candidate = structure(
        "Requirements\n3+ years of Python\nDjango\n"
        f"{heading}\nAcme is a global leader in widgets, with offices in 30 countries.\n"
    )
    assert heading not in required_skill_names(candidate)
    assert not any("global leader" in item.description for item in candidate.qualifications)
    assert required_tech(candidate) == {"Python", "Django"}


@pytest.mark.parametrize(
    "heading",
    [
        "Bonus Skills",
        "Bonus Qualifications",
        "Plus",
        "Pluses",
        "Optional",
        "Extras",
        "Would be nice",
        "What would be nice",
        "Would be a plus",
        "Great to have",
        "Even better if you have",
        "Stand out if you have",
        "Preferred but not required",
        "Additional Skills",
        # Not in any vocabulary: the optional cue (`_OPTIONAL_CUE`).
        "Nice extras",
        "Bonus know how",
        "Ideally you would have",
        "The Ideal Candidate",
        "Even better if you’ve got",
    ],
)
def test_an_optional_heading_inside_an_unbulleted_list_opens_preferred(heading):
    """No blank line, no colon, no capitals: no mark fires, and the heading used
    to be filed as a REQUIRED skill with every optional line beneath it."""
    candidate = structure(
        f"Requirements\n3+ years of Python\nDjango\n{heading}\nKubernetes\nTerraform\n"
    )
    assert required_tech(candidate) == {"Python", "Django"}
    assert {"Kubernetes", "Terraform"} <= preferred_tech(candidate)
    assert heading not in required_skill_names(candidate)


@pytest.mark.parametrize(
    "text",
    [
        "Requirements\n- Python\n- Django\nBonus Skills\n- Kubernetes\n- Terraform\n",
        "Requirements\nPython\nDjango\nBonus Skills:\nKubernetes\nTerraform\n",
    ],
)
def test_a_marked_optional_heading_opens_preferred_rather_than_dropping_its_items(text):
    candidate = structure(text)
    assert required_tech(candidate) == {"Python", "Django"}
    assert {"Kubernetes", "Terraform"} <= preferred_tech(candidate)


@pytest.mark.parametrize(
    "item, technology",
    [
        ("Kubernetes a plus", "Kubernetes"),
        ("Plus experience with Kafka", "Kafka"),
        ("Extra mile attitude", None),
    ],
)
def test_an_item_that_merely_contains_an_optional_word_is_not_a_heading(item, technology):
    """The cue reads the FIRST word only, and not "Plus" or "Extra mile": these
    are items, and reading one as a heading would drop it and demote the
    required line after it."""
    candidate = structure(f"Requirements\nPython\n{item}\nDjango\n")
    assert {"Python", "Django"} <= required_tech(candidate)
    if technology:
        assert technology in {t.name for t in candidate.technologies}


@pytest.mark.parametrize(
    "heading",
    [
        "Key Responsibilities",
        "Your responsibilities",
        "Duties",
        "Main Duties",
        "Essential Duties and Responsibilities",
        "Your Role",
        "What You Will Do",
        "What you’ll be working on",
    ],
)
def test_a_duties_heading_after_unbulleted_requirements_opens_responsibilities(heading):
    candidate = structure(
        f"Requirements\n3+ years of Python.\nExperience with AWS.\n{heading}\nWrite code\nReview code\n"
    )
    assert required_tech(candidate) == {"Python", "AWS"}
    assert [r.description for r in candidate.responsibilities] == ["Write code", "Review code"]
    assert candidate.required_skills == []


def test_the_optional_cue_does_not_turn_perks_after_duties_into_skills():
    """Under duties the first-word cue is not asked: nothing there can be
    promoted to required, and a perks line would otherwise open PREFERRED."""
    candidate = structure("What you'll do\nBuild services\nNice office in the centre\nFree lunch\n")
    assert candidate.preferred_skills == []
    assert candidate.required_skills == []


def test_a_values_heading_after_unbulleted_requirements_closes_them():
    candidate = structure(
        "Requirements\n3+ years of Python.\nExperience with AWS.\nOur Core Values\nCustomer obsession\nBias for action\n"
    )
    assert required_tech(candidate) == {"Python", "AWS"}
    assert candidate.required_skills == []


@pytest.mark.parametrize(
    "text, technologies",
    [
        (
            "Requirements\n5+ years of Python\nWorking with Node.js and React\n3+ years of PostgreSQL\n"
            "Experience with Kubernetes, Docker\n",
            {"Python", "Node.js", "React", "PostgreSQL", "Kubernetes", "Docker"},
        ),
        ("Requirements\nPython\nWorking with AWS\nDocker\nKubernetes\n", {"Python", "AWS", "Docker", "Kubernetes"}),
        ("Requirements\nAbout 5 years of Python experience\nDjango\n", {"Python", "Django"}),
        ("Requirements\n3+ years of Python\nAbout 3 years of Django.\n5+ years SQL\n", {"Python", "Django", "SQL"}),
    ],
)
def test_a_requirement_that_starts_like_a_company_heading_is_read(text, technologies):
    """`_OTHER_HEADINGS` is matched before any shape check, and its open-ended
    forms used to take digits and dots: each of these closed its section on a
    requirement line and dropped everything after it."""
    assert required_tech(structure(text)) == technologies


def test_a_duty_that_starts_like_a_company_heading_is_read():
    candidate = structure(
        "Responsibilities\nBuild backend services\nWorking with 3rd-party APIs\nMentor engineers on the team.\n"
    )
    assert len(candidate.responsibilities) == 3


# --- pay after the last requirement ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "What you'll do\n- Build payment APIs in Python.\n- Own services end-to-end.\n\n"
        "The base pay range for this role is $150,000 - $180,000.\nBonus\nEquity\n401(k) matching\n",
        "Requirements\n- 5+ years of Python\n- Experience with AWS\n\nPay range: $150,000 - $180,000\nBonus\nEquity\n",
        "Requirements\nPython\nAWS\nPay range: $150k-$180k\nBonus\nEquity\nStock options\n",
        # No pay line: what follows "Bonus" says it is pay.
        "Requirements\nPython\nAWS\nBonus\nEquity\n401(k) matching\n",
    ],
)
def test_bonus_in_a_pay_block_after_the_requirements_is_not_a_preferred_skill(text):
    candidate = structure(text)
    assert candidate.preferred_skills == []
    assert not any("$" in q.description for q in candidate.qualifications)
    assert not {"Equity", "Stock options", "401(k) matching"} & set(required_skill_names(candidate))


@pytest.mark.parametrize(
    "line",
    ["Compensation planning experience", "Salary benchmarking", "Payroll systems"],
)
def test_a_competency_starting_with_a_pay_word_is_still_a_requirement(line):
    candidate = structure(f"Requirements\nPython\n{line}\nDjango\n")
    assert line in required_skill_names(candidate)
    assert required_tech(candidate) == {"Python", "Django"}


# --- typographic apostrophes ---------------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    ["What you’ll bring", "What you’ll need", "What we’re looking for", "What you‘ll bring"],
)
def test_a_heading_with_a_typographic_apostrophe_is_recognised(heading):
    """Rich text writes U+2019. Unrecognised, the required list under it was
    skipped and only the preferred list survived — the inversion."""
    candidate = structure(
        f"About Acme\nWe build widgets.\n{heading}\n- 5+ years of Python\n- Django\nNice to have\n- Kubernetes\n"
    )
    assert required_tech(candidate) == {"Python", "Django"}
    assert preferred_tech(candidate) == {"Kubernetes"}


# --- the list-style mark and the look-ahead ------------------------------------------


def test_an_unbulleted_lead_in_does_not_disable_the_list_style_mark():
    """The heading after the bullets is unknown on purpose: only mark 4 can
    close the section, and it used to be keyed to the lead-in."""
    candidate = structure(
        "Qualifications\nTo succeed in this role, you will bring the following experience.\n"
        "- 3+ years of Python\n- Experience with Django\nCompany Snapshot\n"
        "Acme is a global leader in widgets, with offices in 30 countries...\nPerks at Acme\nWe offer flexible hours\n"
    )
    assert not any("global leader" in q.description for q in candidate.qualifications)
    assert not {"Company Snapshot", "Perks at Acme", "We offer flexible hours"} & set(
        required_skill_names(candidate)
    )
    assert required_tech(candidate) == {"Python", "Django"}


def test_the_next_non_empty_line_is_found_for_every_line():
    assert _next_non_empty_lines(["a", "", " ", "b", "c", ""]) == ["b", "b", "b", "c", None, None]


def test_the_look_ahead_adds_no_per_line_cost():
    """The look-ahead runs only for heading-shaped (unbulleted) lines. It used to
    rescan the rest of the description for each one — quadratic, 1.3 s at the
    50,000-character limit — so the unbulleted reading of a long list cost
    about twice the bulleted one. Linear, the two cost the same."""

    def best_of_three(text: str) -> float:
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            DeterministicJobStructurer()._read(text)
            best = min(best, time.perf_counter() - started)
        return best

    unbulleted = best_of_three("Requirements\n" + "Abc\n" * 12_000)
    bulleted = best_of_three("Requirements\n" + "- Abc\n" * 12_000)
    assert unbulleted < 1.4 * bulleted


def test_bonus_under_compensation_is_still_a_pay_line():
    """The contextual rule: "Bonus" heads optional skills only where
    requirements are already being read."""
    candidate = structure("Compensation\nBase salary\nBonus\nEquity\n")
    assert candidate.preferred_skills == []
    assert candidate.required_skills == []


# --- degrees ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, level",
    [
        ("BA in Economics", "bachelor"),
        ("BS, Computer Science", "bachelor"),
        ("BS required", "bachelor"),
        ("B.S. or equivalent", "bachelor"),
        ("MS or equivalent", "master"),
        ("MS. in Computer Science", "master"),
        ("M.S.in Computer Science", "master"),
        ("Masters preferred", "master"),
        ("Masters or equivalent", "master"),
        # The second review: short forms with a field and no "in", each filed
        # as a REQUIRED SKILL by the first context rule.
        ("BS Computer Science", "bachelor"),
        ("BS Computer Science or related field", "bachelor"),
        ("BS CS or equivalent", "bachelor"),
        ("MS Computer Science preferred", "master"),
        ("MS CS", "master"),
        ("B.S. Computer Science", "bachelor"),
        ("BS: Computer Science", "bachelor"),
        ("BS - Computer Science", "bachelor"),
        ("BS. Computer Science", "bachelor"),
        ("BA Economics", "bachelor"),
        ("BA (Hons) Economics", "bachelor"),
        ("Master in Computer Science", "master"),
        ("Masters of Science in CS", "master"),
        ("Masters' degree", "master"),
        ("Masters level education", "master"),
        ("Candidate has a BA in Economics", "bachelor"),
        ("Computer Science, MS preferred", "master"),
    ],
)
def test_a_degree_written_in_capitals_is_read_wherever_it_is_plainly_a_degree(line, level):
    candidate = structure(f"Requirements\n- {line}\n")
    assert [item.degree_level for item in candidate.education] == [level], candidate.education
    assert required_skill_names(candidate) == []


@pytest.mark.parametrize(
    "line",
    [
        "Proficient in MS Office",
        "Experience as a BA in fintech",
        "Based in Boston, MA",
        # The second review: what the BA/MS comment promised and the code did not do.
        "a BA in a fintech team",
        "Worked as BA in fintech",
        "Prior BA in banking",
        "Experience as a senior BA in fintech",
        "Business Analyst (BA) experience",
        "Must be able to commute to Jackson, MS",
        "Starkville, MS or remote",
        # And what a field vocabulary must not let back in.
        "MS SQL Server",
        "MS Teams",
        "MS Business Central",
        "Scrum Master in an agile team",
        "Knowledge of BS 7671",
        "Report to M. Sanchez",
    ],
)
def test_the_same_letters_that_are_not_a_degree_are_still_refused(line):
    candidate = structure(f"Requirements\n- {line}\n")
    assert candidate.education == []


# --- technologies -------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, technology",
    [
        ("Experience building Rest APIs", "REST"),
        ("Experience building rest APIs", "REST"),
        ("Strong JS/TS skills", "TypeScript"),
    ],
)
def test_a_guarded_technology_is_read_where_the_context_is_unambiguous(line, technology):
    candidate = structure(f"Requirements\n- {line}\n")
    assert technology in {t.name for t in candidate.technologies}


@pytest.mark.parametrize("line", ["Work with the rest of the team", "Active TS/SCI clearance"])
def test_the_english_or_clearance_reading_is_still_refused(line):
    candidate = structure(f"Requirements\n- {line}\n")
    assert not {"REST", "TypeScript"} & {t.name for t in candidate.technologies}
