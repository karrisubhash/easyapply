"""Regressions the adversarial review of the resume-ingestion fixes found.

Each fix in that pass made one resume shape read better; each of these is a
shape it made read WORSE, found by probing ordinary resumes against the
baseline before release. The whole-line heading rule, the page-header reading
and the one-certification-per-line rule all stay — these pin the cases they
first got wrong.
"""

from __future__ import annotations

import pytest

from app.services.resume.extraction import (
    FURNITURE_LINES_KEY,
    ExtractedDocument,
    ExtractedPage,
)
from app.services.resume.mapping import to_snapshot
from app.services.resume.structuring import DeterministicResumeStructurer, _heading


def _read(text: str, **metadata):
    document = ExtractedDocument(
        pages=[ExtractedPage(page_number=1, text=text, method="text", metadata=metadata)]
    )
    return DeterministicResumeStructurer().structure(document).candidate


# --- compound and qualified headings ------------------------------------------------


@pytest.mark.parametrize(
    "line, section",
    [
        ("Skills & Tools", "skills"),
        ("Skills & Expertise", "skills"),
        ("Education & Training", "education"),
        ("Certifications & Trainings", "certifications"),
        ("Trainings & Certifications", "certifications"),
        ("Professional Certifications", "certifications"),
        ("Certificates", "certifications"),
        ("Summary of Qualifications", "summary"),
        ("Work Experience (Selected)", "experience"),
        ("Experience Summary", "experience"),
        ("Project Experience", "projects"),
        ("Projects Undertaken", "projects"),
        ("Academic Background", "education"),
        ("Academic Qualifications", "education"),
        ("Educational Qualifications", "education"),
    ],
)
def test_a_compound_or_qualified_heading_still_opens_its_section(line, section):
    assert _heading(line) == section


@pytest.mark.parametrize(
    "line",
    ["Project Manager", "Project Manager | Acme Corp | 2020 - Present", "Experience building Django apps"],
)
def test_a_job_title_or_a_sentence_is_still_not_a_heading(line):
    """What the whole-line rule was introduced to fix must stay fixed."""
    assert _heading(line) is None


def test_a_resume_with_compound_headings_is_read_whole():
    """The first version of the whole-line rule lost this resume's summary,
    all five skills, its degree and its certification at once."""
    candidate = _read(
        "\n".join(
            [
                "Priya Sharma",
                "Pune, India | +91 98765 43210 | priya.sharma@example.com",
                "Profile Summary",
                "Backend developer with four years building Python services and data pipelines.",
                "Work Experience",
                "Software Engineer | Infosys | Pune, India",
                "Jun 2021 - Present",
                "• Built REST APIs in Django serving 2M requests a day.",
                "Skills & Tools",
                "Languages: Python, SQL, Java",
                "Frameworks: Django, FastAPI",
                "Education & Training",
                "B.Tech, Computer Engineering | CGPA: 8.4",
                "Savitribai Phule Pune University",
                "2017 - 2021",
                "Certifications & Trainings",
                "AWS Certified Developer | Amazon Web Services | 2023",
            ]
        )
    )
    snapshot, _ = to_snapshot(candidate)
    assert snapshot.summary and snapshot.summary.startswith("Backend developer")
    assert [row.company for row in snapshot.experiences] == ["Infosys"]
    assert {row.name for row in snapshot.skills} == {"Python", "SQL", "Java", "Django", "FastAPI"}
    assert len(snapshot.education) == 1
    assert [row.name for row in snapshot.certifications] == ["AWS Certified Developer"]


# --- a page header is not the candidate's name --------------------------------------


def test_a_curriculum_vitae_page_header_is_not_taken_for_the_name():
    candidate = _read(
        "Curriculum Vitae\nJane Doe\njane.doe@example.com | +1 555 010 2000",
        **{FURNITURE_LINES_KEY: 1},
    )
    assert candidate.contact is not None
    assert candidate.contact.full_name == "Jane Doe"


@pytest.mark.parametrize("title", ["Resume", "RÉSUMÉ", "CV", "Curriculum Vitae"])
def test_a_document_title_is_never_a_name(title):
    candidate = _read(f"{title}\nJane Doe\njane.doe@example.com")
    assert candidate.contact is not None
    assert candidate.contact.full_name == "Jane Doe"


# --- a certification's issuer and details on lines of their own ---------------------


def _certifications(*lines: str):
    return _read("\n".join(["Jane Doe", "Certifications", *lines])).certifications


def test_an_issuer_on_its_own_line_is_the_issuer_not_a_second_certification():
    certifications = _certifications(
        "Google Data Analytics Professional Certificate",
        "Coursera",
        "AWS Certified Developer – Associate",
        "Amazon Web Services",
    )
    assert [(c.name, c.issuing_organization) for c in certifications] == [
        ("Google Data Analytics Professional Certificate", "Coursera"),
        ("AWS Certified Developer – Associate", "Amazon Web Services"),
    ]


def test_an_unknown_short_line_is_the_next_certification_not_an_issuer():
    """Only a known issuer is read as one: "PMP" under "CKA" is a credential."""
    certifications = _certifications("CKA", "PMP")
    assert [(c.name, c.issuing_organization) for c in certifications] == [("CKA", None), ("PMP", None)]


def test_a_linkedin_style_detail_line_attaches_every_detail():
    [certification] = _certifications(
        "AWS Certified Solutions Architect",
        "Issued Mar 2020 · Expires Mar 2023",
        "Credential ID ABC123",
    )
    assert certification.issue_date is not None and certification.issue_date.raw == "Mar 2020"
    assert certification.expiration_date is not None and certification.expiration_date.raw == "Mar 2023"
    assert certification.credential_id == "ABC123"
