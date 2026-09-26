"""The golden resume: one document, asserted fact by fact.

This exists because a green E2E did not mean the profile was right. The old
suite proved the pipeline reached `completed`, and it did — while turning one
job into twelve unmappable rows, dropping every project and certification, and
losing the candidate's name, summary and every link on the document. A run that
finishes is not a run that worked.

So every assertion here names one fact the document states, and fails on its
own. `expected_profile.json` was written from the resume before the pipeline was
ever run against it, and it is not to be edited to match an output: if the code
disagrees with it, the question is which one is wrong about the document.

Assertions are field-appropriate, as normalisation legitimately differs:
`job_title` is exact because the resume gives it exactly, `company` is a
substring because keeping "(Andhra Pradesh Transmission Corporation)" on the end
of an employer is not an error. What is never relaxed is the count of a section
or the absence of something the resume does not say.
"""

import json
import re
from pathlib import Path

import pytest

from app.services.resume.extraction import extract
from app.services.resume.mapping import to_snapshot
from app.services.resume.structuring import (
    DeterministicResumeStructurer,
    verify_provenance,
)
from evaluation.datasets.resume_profile_v1.resume_source import build_resume_pdf

DATASET = Path(__file__).parent.parent / "evaluation" / "datasets" / "resume_profile_v1"


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads((DATASET / "expected_profile.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def document():
    return extract(build_resume_pdf(), media_type="application/pdf")


@pytest.fixture(scope="module")
def candidate(document):
    return DeterministicResumeStructurer().structure(document).candidate


@pytest.fixture(scope="module")
def mapped(candidate):
    return to_snapshot(candidate)


@pytest.fixture(scope="module")
def snapshot(mapped):
    return mapped[0]


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


# --- extraction --------------------------------------------------------------


def test_the_whole_document_is_extracted_as_one_page(document):
    assert document.page_count == 1
    assert document.total_chars > 1500


def test_headings_survive_extraction(document):
    """Every section heading, spelled as one word.

    pypdf rendered this document's "Education" as "Educa tion", which matched no
    heading pattern, which silently dropped the section. The heading being intact
    is the precondition for everything below it being read at all.
    """
    text = document.pages[0].text
    for heading in (
        "Summary",
        "Technical Skills",
        "Experience",
        "Projects",
        "Certifications",
        "Education",
    ):
        assert f"\n{heading}\n" in f"\n{text}\n", heading


def test_words_are_not_welded_together(document):
    """Spacing is inferred from glyph positions, and getting it wrong is silent.

    A run of 25+ letters is not a word in this document; it is two or three with
    the spaces missing, which is what an extractor that guesses badly produces.
    """
    text = document.pages[0].text
    assert not re.search(r"[A-Za-z]{25,}", text)
    assert "Languages: Python" in text


def test_every_hyperlink_is_recovered(document, expected):
    """URLs live in the page's annotations, not in its text.

    The text says "GitHub"; the address is somewhere else entirely. Reading only
    the text layer loses every link on the document.
    """
    urls = {link.url for link in document.pages[0].links}
    for url in expected["links_include"]:
        assert url in urls, url


def test_no_control_characters_reach_the_text(document):
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", document.pages[0].text)


# --- contact, summary, links -------------------------------------------------


def test_the_candidate_is_named(snapshot, expected):
    assert snapshot.contact is not None, "the resume states a name and an address"
    assert snapshot.contact.full_name == expected["contact"]["full_name"]


def test_contact_details_reach_the_profile(snapshot, expected):
    contact = snapshot.contact
    assert contact.email == expected["contact"]["email"]
    assert _digits(contact.phone) == expected["contact"]["phone_digits"]
    assert expected["contact"]["location_contains"] in (contact.location or "")


def test_the_summary_is_the_candidates_own(snapshot, expected):
    assert snapshot.summary, "the resume has a Summary section"
    for fragment in expected["summary_contains"]:
        assert fragment in snapshot.summary, fragment


def _every_link(snapshot):
    """Personal links and each project's own links, together.

    `expected_profile.json` lists every link the document holds, and it is
    frozen. Since candidate schema v3 split ownership, a link is stored on its
    owner — the header's LinkedIn/GitHub/Portfolio on `snapshot.links`, a
    project's repository and Demo on that project — so "reaches the profile"
    means reaches EITHER. Reading only `snapshot.links` asked for project links
    in the one place the split exists to keep them out of, and these two tests
    failed for that reason alone once project links were mapped at all.
    """
    return [*snapshot.links, *(link for project in snapshot.projects for link in project.links)]


def test_links_reach_the_profile(snapshot, expected):
    links = _every_link(snapshot)
    urls = {link.url for link in links}
    for url in expected["links_include"]:
        assert url in urls, url
    assert len(links) == expected["exact_counts"]["links"]


def test_links_keep_the_words_they_were_written_as(snapshot):
    labels = {link.label for link in _every_link(snapshot)}
    assert {"LinkedIn", "GitHub", "Portfolio", "Demo"} <= labels


# --- experience --------------------------------------------------------------


def test_every_role_is_read_once(snapshot, expected):
    """The count is the assertion that catches the original failure.

    One role became twelve rows, because every bullet and every wrapped line was
    read as the start of a new job.
    """
    assert len(snapshot.experiences) == expected["exact_counts"]["experiences"]


@pytest.mark.parametrize("index", [0, 1])
def test_each_role_keeps_its_employer_title_and_dates(snapshot, expected, index):
    produced = snapshot.experiences
    assert len(produced) > index
    # Ordered newest first by the profile schema, which is the order the resume
    # lists them in.
    item = produced[index]
    want = expected["experiences"][index]

    assert item.job_title == want["job_title"]
    assert want["company_contains"] in item.company
    assert want["location_contains"] in (item.location or "")
    assert item.start_date.isoformat() == want["start_date"]
    assert item.start_date_precision == want["start_date_precision"]
    if want["end_date"] is None:
        assert item.end_date is None, "an ongoing role has no end date"
    else:
        assert item.end_date.isoformat() == want["end_date"]


@pytest.mark.parametrize("index", [0, 1])
def test_each_role_keeps_its_achievements(snapshot, expected, index):
    """The bullets are the substance of a role, and they are what was lost."""
    item = snapshot.experiences[index]
    assert item.description, "the resume gives this role bullet points"
    for fragment in expected["experiences"][index]["description_contains"]:
        assert fragment in item.description, fragment


def test_a_wrapped_bullet_stays_one_sentence(snapshot):
    """A line break inside a bullet is typography, not a new claim."""
    description = snapshot.experiences[0].description
    assert "cutting p99 latency by 63% across 4M daily events." in description


# --- education ---------------------------------------------------------------


def test_education_is_read_once_with_its_institution(snapshot, expected):
    assert len(snapshot.education) == expected["exact_counts"]["education"]
    item = snapshot.education[0]
    want = expected["education"][0]
    assert want["institution_contains"] in item.institution
    assert item.degree == want["degree"]
    assert want["field_of_study_contains"] in (item.field_of_study or "")


def test_a_stated_grade_is_kept(snapshot, expected):
    item = snapshot.education[0]
    assert expected["education"][0]["grade_contains"] in (item.grade or "")


def test_education_dates_keep_year_precision(snapshot, expected):
    item = snapshot.education[0]
    want = expected["education"][0]
    assert item.start_date.isoformat() == want["start_date"]
    assert item.start_date_precision == want["start_date_precision"]
    assert item.end_date.isoformat() == want["end_date"]
    assert item.end_date_precision == want["end_date_precision"]


# --- skills ------------------------------------------------------------------


def test_every_listed_skill_is_present(snapshot, expected):
    names = {item.name for item in snapshot.skills}
    missing = [name for name in expected["skills_include"] if name not in names]
    assert not missing, f"the resume lists these and they are absent: {missing}"


def test_a_skill_wrapped_across_two_lines_is_one_skill(snapshot):
    """"...GitHub Actions, Load" / "Testing" is one skill called Load Testing."""
    names = {item.name for item in snapshot.skills}
    assert "Load Testing" in names
    assert "Load" not in names and "Testing" not in names


def test_the_row_label_becomes_the_category_not_part_of_the_skill(snapshot, expected):
    categories = {item.category for item in snapshot.skills}
    for category in expected["skill_categories_include"]:
        assert category in categories, category
    assert {item.name for item in snapshot.skills if item.category == "Languages"} == {
        "Python",
        "Go",
        "SQL",
        "TypeScript",
    }


def test_no_skill_carries_its_label_in_its_name(snapshot):
    for item in snapshot.skills:
        assert ":" not in item.name, item.name


def test_skills_are_counted_exactly(snapshot, expected):
    assert len(snapshot.skills) == expected["exact_counts"]["skills"]


# --- projects ----------------------------------------------------------------


def test_projects_are_read(snapshot, expected):
    """Projects were matched as a heading and then never read at all."""
    assert len(snapshot.projects) == expected["exact_counts"]["projects"]


@pytest.mark.parametrize("index", [0, 1])
def test_each_project_keeps_its_name_technologies_and_description(
    snapshot, expected, index
):
    by_name = {item.name: item for item in snapshot.projects}
    want = expected["projects"][index]
    assert want["name"] in by_name, f"{want['name']} is missing"
    item = by_name[want["name"]]

    assert item.technologies == want["technologies"]
    assert item.description
    for fragment in want["description_contains"]:
        assert fragment in item.description, fragment


def test_a_link_caption_is_not_a_technology(snapshot):
    """"| Python, PostgreSQL, Celery GitHub | Demo" lists three technologies.

    "GitHub" and "Demo" are the captions on two hyperlinks that the layout put on
    the same line, and recording them as things the project was built with is a
    claim the resume does not make.
    """
    for item in snapshot.projects:
        assert "GitHub" not in item.technologies
        assert "Demo" not in item.technologies


# --- certifications ----------------------------------------------------------


def test_certifications_are_read(snapshot, expected):
    assert len(snapshot.certifications) == expected["exact_counts"]["certifications"]


@pytest.mark.parametrize("index", [0, 1])
def test_each_certification_keeps_its_issuer_and_date(snapshot, expected, index):
    want = expected["certifications"][index]
    matches = [
        item
        for item in snapshot.certifications
        if want["name_contains"] in item.name
    ]
    assert matches, f"{want['name_contains']} is missing"
    item = matches[0]
    assert want["issuing_organization_contains"] in (item.issuing_organization or "")
    assert item.issue_date.isoformat() == want["issue_date"]


def test_a_section_we_cannot_hold_still_ends_the_one_above_it(snapshot):
    """"Achievements" has nowhere to go, and must not become a certification.

    An unrecognised heading leaves the previous section open, which is how the
    achievements list used to be read as more projects.
    """
    names = {item.name for item in snapshot.certifications}
    assert not any("PyCon" in name for name in names)
    assert not any("PyCon" in item.name for item in snapshot.projects)


# --- nothing invented --------------------------------------------------------


def test_nothing_the_resume_does_not_say_appears(snapshot, expected):
    """Every entry here is a reading the pipeline actually produced before the fix."""
    forbidden = expected["must_not_appear"]

    names = {item.name for item in snapshot.skills}
    assert not names & set(forbidden["skill_names"])

    titles = {item.job_title for item in snapshot.experiences}
    assert not titles & set(forbidden["job_titles"])

    companies = {item.company for item in snapshot.experiences}
    assert not companies & set(forbidden["companies"])

    project_names = {item.name for item in snapshot.projects}
    assert not project_names & set(forbidden["project_names"])

    technologies = {tech for item in snapshot.projects for tech in item.technologies}
    assert not technologies & set(forbidden["project_technologies"])

    degrees = {item.degree for item in snapshot.education}
    assert not degrees & set(forbidden["education_degrees"])


def test_no_grade_is_invented(snapshot):
    """A grade is the classic hallucination, per the candidate schema itself."""
    assert all(
        item.grade is None or any(ch.isdigit() or ch.isalpha() for ch in item.grade)
        for item in snapshot.education
    )


def test_nothing_the_resume_states_is_reported_as_unmappable(mapped):
    """Unmapped items are honest, but on this resume there is nothing to drop."""
    _, unmapped = mapped
    assert unmapped == [], [item.model_dump() for item in unmapped]


def test_every_claim_cites_text_that_is_really_on_the_page(candidate, document):
    """Provenance is checked, not trusted.

    A citation that cannot be found on the page it names is a fabricated piece of
    evidence, and this structurer quotes the lines it actually read — so all of
    them must verify.
    """
    verify_provenance(candidate, document)
    unverified = [
        (entry.page, entry.quote)
        for item in candidate.items()
        for entry in item.provenance
        if not entry.verified
    ]
    assert unverified == []


def test_every_item_carries_a_citation(candidate):
    for item in candidate.items():
        assert item.provenance, f"{type(item).__name__} cites nothing"
