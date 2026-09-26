"""Grounding validation: what a generated resume is and is not allowed to say.

These are the tests the whole tailoring feature rests on. Everything else in the
domain — the state machine, the queue, the renderer, the download gate — is
plumbing that becomes worthless the moment the validator can be talked into
approving a claim the profile does not support.

So the cases here are written as attacks rather than as coverage: each one is a
specific lie a resume generator is known to tell, and the assertion is that the
document carrying it cannot be approved.
"""

from datetime import date

import pytest

from app.models.tailoring import SEVERITY_ERROR, SEVERITY_WARNING
from app.schemas.tailored_resume import (
    TailoredContact,
    TailoredEducation,
    TailoredExperience,
    TailoredLine,
    TailoredLink,
    TailoredProject,
    TailoredResumeDocument,
    TailoredSkillGroup,
)
from app.services.tailoring import grounding
from app.services.tailoring.grounding import (
    REASON_CONTACT_ALTERED,
    REASON_FIELD_ALTERED,
    REASON_QUOTE_NOT_FOUND,
    REASON_UNKNOWN_LINK,
)
from tests.matching_fixtures import (
    a_project_row,
    a_skill_row,
    a_version,
    an_education_row,
    an_experience_row,
)

DESCRIPTION = (
    "Built the payments ingestion service in Python. "
    "Migrated the reporting pipeline onto PostgreSQL. "
    "Mentored two new engineers through onboarding."
)


@pytest.fixture
def version():
    """One job, one project, one skill, one degree. Enough to lie about."""
    return a_version(
        experiences=[
            an_experience_row(
                company="Acme Corp",
                job_title="Backend Engineer",
                start=date(2021, 3, 1),
                end=date(2024, 6, 1),
                description=DESCRIPTION,
                id=101,
            )
        ],
        projects=[
            a_project_row(name="EasyApply", technologies=["FastAPI", "PostgreSQL"], id=201),
        ],
        skills=[a_skill_row("Python", id=301)],
        education=[
            an_education_row(
                institution="State University", degree="BSc", field_of_study="Computer Science", id=401
            )
        ],
    )


def a_bullet(text: str, quote: str = DESCRIPTION, field: str = "description") -> TailoredLine:
    return TailoredLine(text=text, source_quote=quote, source_field=field)


def an_entry(**overrides) -> TailoredExperience:
    # One verbatim bullet by default: a job whose row has quotable text and
    # which carries nothing under it is now an error of its own
    # (REASON_NO_BULLETS), and the cases here are about other things.
    base = dict(
        source_id=101,
        company="Acme Corp",
        job_title="Backend Engineer",
        start_date=date(2021, 3, 1),
        end_date=date(2024, 6, 1),
        bullets=[a_bullet("Built the payments ingestion service in Python.")],
    )
    base.update(overrides)
    return TailoredExperience(**base)


def a_document(**overrides) -> TailoredResumeDocument:
    base = dict(experience=[an_entry()])
    base.update(overrides)
    return TailoredResumeDocument(**base)


def reasons(report) -> list[str]:
    return [finding.reason for finding in report.findings]


# --- the baseline ----------------------------------------------------------------


def test_a_document_that_only_quotes_the_profile_passes(version):
    document = a_document(
        experience=[
            an_entry(
                bullets=[a_bullet("Built the payments ingestion service in Python.")]
            )
        ]
    )

    report = grounding.validate(document, version)

    assert report.passed
    assert report.errors == ()


def test_an_empty_document_is_not_approved(version):
    report = grounding.validate(TailoredResumeDocument(), version)

    assert not report.passed
    assert grounding.REASON_EMPTY_DOCUMENT in reasons(report)


# --- inventing facts ---------------------------------------------------------------


def test_an_invented_number_is_an_error(version):
    """The classic hallucination: a metric nobody measured.

    The profile says two engineers were mentored. It says nothing about a
    percentage, and a resume that claims one is a resume the candidate cannot
    defend in the interview it wins them.
    """
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet("Mentored two new engineers, cutting onboarding time by 40%.")
                ]
            )
        ]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_INVENTED_NUMBER in reasons(report)


def test_a_number_the_profile_does_state_is_fine(version):
    document = a_document(
        experience=[an_entry(bullets=[a_bullet("Mentored two new engineers through onboarding.")])]
    )

    assert grounding.validate(document, version).passed


def test_an_invented_technology_is_an_error(version):
    """A name that appears nowhere in the profile is an invented name."""
    document = a_document(
        experience=[
            an_entry(bullets=[a_bullet("Built the payments ingestion service in Kubernetes.")])
        ]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_INVENTED_NAME in reasons(report)


def test_a_name_from_elsewhere_in_the_profile_is_only_a_warning(version):
    """Borrowing across rows is a judgement call, not a fabrication.

    "FastAPI" is on the project, not on this job. Putting it in this job's
    bullet is something a reader should see and decide about — it is not a
    claim the profile contradicts, so it does not withhold the artifact.
    """
    document = a_document(
        experience=[
            an_entry(bullets=[a_bullet("Built the payments ingestion service in FastAPI.")])
        ]
    )

    report = grounding.validate(document, version)

    assert report.passed
    assert grounding.REASON_BORROWED_NAME in reasons(report)
    assert all(finding.severity == SEVERITY_WARNING for finding in report.findings)


def test_rewording_is_a_warning_not_an_error(version):
    """Swapping a verb changes the prose, not the facts."""
    document = a_document(
        experience=[
            an_entry(bullets=[a_bullet("Delivered the payments ingestion service in Python.")])
        ]
    )

    report = grounding.validate(document, version)

    assert report.passed


# --- faking the citation ------------------------------------------------------------


def test_a_quote_that_is_not_in_the_profile_is_an_error(version):
    """A fabricated citation is worse than none: it is what makes the rest of
    the line look checked."""
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet(
                        "Led the platform rewrite.",
                        quote="Led the platform rewrite end to end.",
                    )
                ]
            )
        ]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_QUOTE_NOT_FOUND in reasons(report)


def test_a_quote_from_the_wrong_column_is_an_error(version):
    """The company name is real text on this row, and it is not a description.

    A citation has to name where it came from or it cannot be checked, and
    letting a quote match any column would make `source_field` decorative.
    """
    document = a_document(
        experience=[an_entry(bullets=[a_bullet("Acme Corp", quote="Acme Corp", field="description")])]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_QUOTE_NOT_FOUND in reasons(report)


def test_a_bad_quote_reports_once_not_once_per_word(version):
    """One accurate finding beats twenty derived from the same cause."""
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet(
                        "Directed a global transformation programme across eleven markets.",
                        quote="Directed a global transformation programme.",
                    )
                ]
            )
        ]
    )

    report = grounding.validate(document, version)

    assert len(report.errors) == 1
    assert report.errors[0].reason == grounding.REASON_QUOTE_NOT_FOUND


# --- altering what was supposed to be copied ------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("company", "Acme Corporation International"),
        ("job_title", "Senior Staff Backend Engineer"),
    ],
)
def test_an_improved_identity_field_is_an_error(version, field, value):
    """A promoted title is a lie with a straight face."""
    document = a_document(experience=[an_entry(**{field: value})])

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_FIELD_ALTERED in reasons(report)


def test_a_stretched_date_is_an_error(version):
    document = a_document(experience=[an_entry(start_date=date(2019, 1, 1))])

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_FIELD_ALTERED in reasons(report)


def test_casing_and_whitespace_differences_are_forgiven(version):
    document = a_document(experience=[an_entry(company="ACME   Corp")])

    assert grounding.validate(document, version).passed


# --- claiming rows that do not exist ---------------------------------------------------


def test_an_entry_pointing_at_nothing_is_an_error(version):
    """The document is describing a career the user does not have."""
    document = a_document(experience=[an_entry(source_id=999)])

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_UNKNOWN_SOURCE in reasons(report)


def test_an_education_entry_from_another_version_is_an_error(version):
    document = a_document(
        education=[TailoredEducation(source_id=888, institution="State University")]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_UNKNOWN_SOURCE in reasons(report)


# --- claiming skills and technologies ----------------------------------------------------


def test_a_skill_the_profile_does_not_list_is_an_error(version):
    document = a_document(
        skills=[TailoredSkillGroup(category="Relevant to this role", skills=["Python", "Rust"])]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_UNLISTED_SKILL in reasons(report)


def test_a_project_technology_counts_as_a_listed_skill(version):
    """The skills section may name a technology a project used.

    Two places record the same kind of fact, and a user who put FastAPI on a
    project rather than in their skills list has still said they use it.

    Grouped under "Other" because this profile stores no categories, and a
    heading is now checked too (an invented one is an error of its own; see
    `test_tailoring_grounding_hardening.py`).
    """
    document = a_document(
        skills=[TailoredSkillGroup(category="Other", skills=["Python", "FastAPI"])]
    )

    assert grounding.validate(document, version).passed


def test_a_technology_added_to_a_project_is_an_error(version):
    document = a_document(
        projects=[
            TailoredProject(
                source_id=201,
                name="EasyApply",
                technologies=["FastAPI", "PostgreSQL", "Kafka"],
            )
        ]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_UNLISTED_TECHNOLOGY in reasons(report)


# --- the summary, which cites the profile as a whole ---------------------------------------


def test_a_summary_line_must_still_be_quoted_from_somewhere(version):
    document = a_document(
        summary=[a_bullet("Seasoned leader with a decade of platform experience.", quote="Seasoned leader")]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_QUOTE_NOT_FOUND in reasons(report)


def test_a_summary_line_quoting_any_experience_description_is_accepted(version):
    document = a_document(
        summary=[a_bullet("Migrated the reporting pipeline onto PostgreSQL.")]
    )

    assert grounding.validate(document, version).passed


@pytest.mark.parametrize(
    "field",
    ["id", "start_date", "profile_version_id", "profile_version", "metadata", "not_a_column"],
)
def test_a_summary_line_citing_a_field_that_holds_no_text_is_refused_not_raised(version, field):
    """`source_field` is generator output, so it is any string at all.

    A non-text column, a relationship or a mapper internal is not a haystack and
    never was. Before this was guarded, joining them raised TypeError, and the
    only thing `pipeline._run_stages` could do with that was FAIL the run —
    losing the document and telling the user "something went wrong" about the
    one stage whose contract says it cannot fail a run. It is an ordinary bad
    citation, and it reads as one.
    """
    document = a_document(
        summary=[a_bullet("Migrated the reporting pipeline onto PostgreSQL.", field=field)]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_QUOTE_NOT_FOUND in reasons(report)


# --- targeting ---------------------------------------------------------------------------


def test_claiming_to_target_a_requirement_the_posting_lacks_is_a_warning(version):
    """Being wrong about what you optimised for does not make the document
    untrue, so it does not withhold the file."""
    document = a_document(targeted_requirements=["Python", "Fortran"])

    report = grounding.validate(
        document, version, job_requirement_labels=frozenset({"Python"})
    )

    assert report.passed
    assert grounding.REASON_UNTARGETED in reasons(report)
    assert report.warnings[0].severity == SEVERITY_WARNING


# --- the verdict itself --------------------------------------------------------------------


def test_errors_and_warnings_are_counted_separately(version):
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet("Built the payments ingestion service in Kubernetes."),
                    a_bullet("Delivered the payments ingestion service in FastAPI."),
                ]
            )
        ]
    )

    report = grounding.validate(document, version)

    assert len(report.errors) == 1
    assert len(report.warnings) >= 1
    assert all(finding.severity == SEVERITY_ERROR for finding in report.errors)


# --- contact: the fields that made the document a resume ----------------------------
#
# These exist because contact details were deliberately kept OUT of the tailored
# document schema, on the reasoning that a field a generator can fill in is a
# field it will invent. That reasoning was right about `years_of_experience` and
# wrong about a name: the difference is not "is it about the person", it is "can
# anything check it". A name, an email, a phone number and a location are
# columns on the version, so they are checked exactly — the same check, of the
# same strictness, that catches a promoted job title.
#
# Leaving them out did not prevent a fabrication. It shipped a flawlessly
# grounded DOCX with no name on it and no way for an employer to reply.


@pytest.fixture
def version_with_contact():
    return a_version(
        experiences=[
            an_experience_row(
                company="Acme Corp",
                job_title="Backend Engineer",
                start=date(2021, 3, 1),
                end=date(2024, 6, 1),
                description=DESCRIPTION,
                id=101,
            )
        ],
        full_name="Alex Morgan",
        email="alex@example.com",
        phone="+44 7700 900123",
        location="Bristol, United Kingdom",
        summary="Backend engineer focused on distributed systems.",
        links=[("https://github.com/alex", "GitHub")],
    )


def a_contact(**overrides) -> TailoredContact:
    base = dict(
        full_name="Alex Morgan",
        email="alex@example.com",
        phone="+44 7700 900123",
        location="Bristol, United Kingdom",
    )
    base.update(overrides)
    return TailoredContact(**base)


def test_contact_copied_from_the_profile_passes(version_with_contact):
    report = grounding.validate(a_document(contact=a_contact()), version_with_contact)

    assert report.passed
    assert not report.findings


@pytest.mark.parametrize(
    "field,value",
    [
        ("full_name", "Alexander Morgan"),
        ("email", "alex.morgan@example.com"),
        ("phone", "+447700900123"),
        ("location", "London, United Kingdom"),
    ],
)
def test_an_altered_contact_detail_is_an_error(version_with_contact, field, value):
    """Including the tidy-ups. A phone number reformatted into international
    style is still a different string from the one the user confirmed, and a
    resume carrying someone else's email is worse than one carrying none."""
    report = grounding.validate(
        a_document(contact=a_contact(**{field: value})), version_with_contact
    )

    assert not report.passed
    assert REASON_CONTACT_ALTERED in reasons(report)


def test_an_invented_contact_detail_is_an_error():
    """A version holding no phone number cannot produce a document with one."""
    version = a_version(
        experiences=[an_experience_row(description=DESCRIPTION, id=101)],
        full_name="Alex Morgan",
    )

    report = grounding.validate(
        a_document(contact=TailoredContact(full_name="Alex Morgan", phone="+44 7700 900123")),
        version,
    )

    assert not report.passed
    assert REASON_CONTACT_ALTERED in reasons(report)


def test_a_link_the_profile_does_not_hold_is_an_error(version_with_contact):
    """A plausible-looking profile URL is exactly the kind of thing a generator
    completes from a name."""
    report = grounding.validate(
        a_document(
            contact=a_contact(
                links=[TailoredLink(url="https://github.com/alex-morgan", label="GitHub")]
            )
        ),
        version_with_contact,
    )

    assert not report.passed
    assert REASON_UNKNOWN_LINK in reasons(report)


def test_a_link_relabelled_to_something_else_is_an_error(version_with_contact):
    """"GitHub" pointing at a stranger's profile is the same class of mistake as
    an invented employer."""
    report = grounding.validate(
        a_document(
            contact=a_contact(
                links=[TailoredLink(url="https://github.com/alex", label="Portfolio")]
            )
        ),
        version_with_contact,
    )

    assert not report.passed
    assert REASON_CONTACT_ALTERED in reasons(report)


def test_the_profiles_own_summary_is_quotable(version_with_contact):
    """The version's `summary` column lives on the version rather than on any
    child row, so the haystack loop could not reach it and a document quoting
    the candidate's own summary verbatim was reported as citing text that is not
    in the profile."""
    report = grounding.validate(
        a_document(
            contact=a_contact(),
            summary=[
                TailoredLine(
                    text="Backend engineer focused on distributed systems.",
                    source_quote="Backend engineer focused on distributed systems.",
                    source_field="summary",
                )
            ],
        ),
        version_with_contact,
    )

    assert report.passed


def test_a_summary_line_citing_a_summary_the_profile_lacks_is_an_error(version):
    report = grounding.validate(
        a_document(
            summary=[
                TailoredLine(
                    text="Ten years leading platform teams.",
                    source_quote="Ten years leading platform teams.",
                    source_field="summary",
                )
            ]
        ),
        version,
    )

    assert not report.passed
    assert REASON_QUOTE_NOT_FOUND in reasons(report)


# --- date precision: the fact a renderer can invent after grounding has looked ------


def test_a_promoted_education_date_precision_is_an_error(version):
    """The resume said "2015 - 2018". The domain stores that as 2015-01-01 with
    precision "year", and a document claiming precision "month" makes the
    renderer print "Jan 2015" — a month the source never gave, introduced in the
    one field that reaches the page directly."""
    report = grounding.validate(
        a_document(
            education=[
                TailoredEducation(
                    source_id=401,
                    institution="State University",
                    degree="BSc",
                    field_of_study="Computer Science",
                    start_date_precision="month",
                )
            ]
        ),
        version,
    )

    assert not report.passed
    assert REASON_FIELD_ALTERED in reasons(report)
