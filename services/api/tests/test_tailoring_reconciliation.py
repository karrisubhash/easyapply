"""The repair pass: what it removes, what it restores, and what it must never do.

`test_tailoring_grounding.py` is written as a list of attacks. This file is
written as the list of *answers* to them, plus one property that matters more
than any individual answer:

    a repair can only ever REMOVE a claim, or restore a copied field to exactly
    what the snapshot says.

That is asserted rather than argued — `test_a_repair_can_only_ever_remove` walks
the whole repaired document and checks every line, skill, technology and entry
against the original. A repair stage that could add is a second generator with
no prompt and no review, and the only way to know this one is not is to keep
checking.

The cases below are the same lies the grounding suite catches, carried one step
further: given that the validator caught it, what is the document that gets
handed over — and is the user told what happened to it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.tailoring import SEVERITY_WARNING
from app.schemas.tailored_resume import (
    TailoredCertification,
    TailoredContact,
    TailoredEducation,
    TailoredExperience,
    TailoredLine,
    TailoredLink,
    TailoredProject,
    TailoredResumeDocument,
    TailoredSkillGroup,
)
from app.services.tailoring import grounding, reconciliation
from app.services.tailoring.reconciliation import (
    REASON_REMOVED_ENTRY,
    REASON_REMOVED_LINE,
    REASON_REMOVED_LINK,
    REASON_REMOVED_SKILL,
    REASON_REMOVED_TECHNOLOGY,
    REASON_RESTORED_FIELD,
)
from tests.matching_fixtures import (
    a_certification_row,
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
PROJECT_DESCRIPTION = "Reconciles ledger entries nightly against the bank feed."


@pytest.fixture
def version():
    version = _version()
    # `an_experience_row` leaves precision to the column default, which an
    # UNSAVED row does not have. A real snapshot always carries one, and the
    # validator now compares it, so the fixture states it.
    version.experiences[0].start_date_precision = "month"
    version.experiences[0].end_date_precision = "month"
    return version


def _version():
    return a_version(
        full_name="ALEX MORGAN",
        email="alex@example.com",
        phone="+44 7700 900123",
        location="Bristol, United Kingdom",
        summary="Backend engineer focused on payments and data pipelines.",
        links=[("https://github.com/alex", "GitHub")],
        experiences=[
            an_experience_row(
                company="Acme Corp",
                job_title="Backend Engineer",
                location="Berlin",
                start=date(2021, 3, 1),
                end=date(2024, 6, 1),
                description=DESCRIPTION,
                id=101,
            )
        ],
        projects=[
            a_project_row(
                name="EasyApply",
                technologies=["FastAPI", "PostgreSQL"],
                description=PROJECT_DESCRIPTION,
                id=201,
            )
        ],
        skills=[a_skill_row("Python", id=301)],
        education=[
            an_education_row(
                institution="State University",
                degree="BSc",
                field_of_study="Computer Science",
                id=401,
            )
        ],
        certifications=[a_certification_row("AZ-900", issuer="Microsoft", id=501)],
    )


def a_bullet(text: str, quote: str = DESCRIPTION, field: str = "description") -> TailoredLine:
    return TailoredLine(text=text, source_quote=quote, source_field=field)


def an_entry(**overrides) -> TailoredExperience:
    # One verbatim bullet by default: a job whose row has quotable text and
    # which carries nothing under it is an unrepairable error of its own
    # (grounding.REASON_NO_BULLETS), and the cases here are about other things.
    base = dict(
        source_id=101,
        company="Acme Corp",
        job_title="Backend Engineer",
        location="Berlin",
        start_date=date(2021, 3, 1),
        start_date_precision="month",
        end_date=date(2024, 6, 1),
        end_date_precision="month",
        bullets=[a_bullet("Built the payments ingestion service in Python.")],
    )
    base.update(overrides)
    return TailoredExperience(**base)


def a_document(**overrides) -> TailoredResumeDocument:
    # The version holds contact columns, and leaving any of them out is now an
    # error of its own (grounding.REASON_CONTACT_MISSING), so the default
    # document carries them exactly.
    base = dict(
        contact=TailoredContact(
            full_name="ALEX MORGAN",
            email="alex@example.com",
            phone="+44 7700 900123",
            location="Bristol, United Kingdom",
        ),
        experience=[an_entry()],
    )
    base.update(overrides)
    return TailoredResumeDocument(**base)


def repaired(document, version):
    """Repair once, then re-validate, exactly as the pipeline does."""
    report = grounding.validate(document, version)
    outcome = reconciliation.repair(document, version, report)
    return outcome, grounding.validate(outcome.document, version)


def reasons(outcome) -> list[str]:
    return [finding.reason for finding in outcome.repairs]


# --- lines -------------------------------------------------------------------------


def test_a_bullet_citing_text_that_is_not_there_is_removed(version):
    """The commonest way a model fails: a sentence that reads like the profile.

    The rest of the document is untouched, which is the entire reason this pass
    exists — a resume is not worth withholding over one bad citation when the
    other bullets are quoted verbatim.
    """
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet("Built the payments ingestion service in Python."),
                    a_bullet(
                        "Led the migration to Kubernetes.",
                        quote="Led the migration to Kubernetes.",
                    ),
                ]
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    kept = outcome.document.experience[0].bullets
    assert [line.text for line in kept] == [
        "Built the payments ingestion service in Python."
    ]
    assert reasons(outcome) == [REASON_REMOVED_LINE]
    assert all(finding.severity == SEVERITY_WARNING for finding in outcome.repairs)


def test_a_bullet_that_invents_a_number_is_removed(version):
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet("Mentored 14 new engineers through onboarding."),
                    a_bullet("Migrated the reporting pipeline onto PostgreSQL."),
                ]
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert [line.text for line in outcome.document.experience[0].bullets] == [
        "Migrated the reporting pipeline onto PostgreSQL."
    ]


def test_a_summary_line_and_a_headline_are_removed_the_same_way(version):
    document = a_document(
        headline=TailoredLine(
            text="Principal Architect",
            source_quote="Principal Architect",
            source_field="job_title",
        ),
        summary=[
            TailoredLine(
                text="Backend engineer focused on payments and data pipelines.",
                source_quote="Backend engineer focused on payments and data pipelines.",
                source_field="summary",
            ),
            TailoredLine(
                text="Ten years of leadership experience.",
                source_quote="Ten years of leadership experience.",
                source_field="summary",
            ),
        ],
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.headline is None
    assert len(outcome.document.summary) == 1


# --- entries -----------------------------------------------------------------------


def test_an_entry_the_profile_does_not_have_is_removed_whole(version):
    """A job the user never held cannot be corrected into one they did."""
    document = a_document(
        experience=[
            an_entry(bullets=[a_bullet("Migrated the reporting pipeline onto PostgreSQL.")]),
            TailoredExperience(
                source_id=999,
                company="Somewhere Else",
                job_title="VP Engineering",
                start_date=date(2015, 1, 1),
            ),
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert [entry.company for entry in outcome.document.experience] == ["Acme Corp"]
    assert REASON_REMOVED_ENTRY in reasons(outcome)


def test_an_altered_company_name_is_restored_rather_than_dropped(version):
    """The distinction this module turns on.

    An entry naming a job the user really held, with the name written wrong, is
    a transcription error — and the snapshot holds the right answer. Dropping
    the job would remove a true thing from the resume to punish a typo.
    """
    document = a_document(
        experience=[
            an_entry(
                company="Acme Corporation Ltd",
                job_title="Senior Backend Engineer",
                bullets=[a_bullet("Migrated the reporting pipeline onto PostgreSQL.")],
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    entry = outcome.document.experience[0]
    assert entry.company == "Acme Corp"
    assert entry.job_title == "Backend Engineer"
    # The bullet was fine and stayed.
    assert len(entry.bullets) == 1
    assert reasons(outcome) == [REASON_RESTORED_FIELD, REASON_RESTORED_FIELD]


def test_a_promoted_date_precision_is_restored(version):
    """The one alteration that never reaches a finding as text.

    A resume that said "2015" stored as 2015-01-01/year, emitted as "month",
    prints "Jan 2015". Nothing in the document reads wrong; the renderer invents
    the month afterwards. So it is restored, not dropped.
    """
    stored = version.education[0]
    stored.start_date = date(2015, 1, 1)
    stored.start_date_precision = "year"

    document = a_document(
        education=[
            TailoredEducation(
                source_id=401,
                institution="State University",
                degree="BSc",
                field_of_study="Computer Science",
                start_date=date(2015, 1, 1),
                start_date_precision="month",
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.education[0].start_date_precision == "year"


def test_a_certification_the_profile_does_not_hold_is_removed(version):
    document = a_document(
        certifications=[
            TailoredCertification(source_id=501, name="AZ-900", issuing_organization="Microsoft"),
            TailoredCertification(source_id=777, name="AWS Certified Solutions Architect"),
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert [entry.name for entry in outcome.document.certifications] == ["AZ-900"]


# --- sets --------------------------------------------------------------------------


def test_a_skill_the_profile_does_not_list_is_removed_from_its_group(version):
    document = a_document(
        skills=[TailoredSkillGroup(category="Backend", skills=["Python", "Rust"])]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.skills[0].skills == ["Python"]
    assert REASON_REMOVED_SKILL in reasons(outcome)


def test_a_group_left_with_no_skills_is_dropped_rather_than_printed_empty(version):
    document = a_document(
        skills=[TailoredSkillGroup(category="Frontend", skills=["React"])]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.skills == []


def test_a_technology_not_on_that_project_is_removed(version):
    document = a_document(
        projects=[
            TailoredProject(
                source_id=201,
                name="EasyApply",
                technologies=["FastAPI", "Kafka"],
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.projects[0].technologies == ["FastAPI"]
    assert REASON_REMOVED_TECHNOLOGY in reasons(outcome)


# --- contact -----------------------------------------------------------------------


def test_a_tidied_contact_detail_is_restored_to_what_the_profile_says(version):
    """A phone number in international format is a different phone number as far
    as this domain is concerned, and the profile is the one that is right."""
    document = a_document(
        contact=TailoredContact(
            full_name="Alexander Morgan",
            email="alex@example.com",
            phone="+44 (0) 7700 900 123",
            location="Bristol, United Kingdom",
        )
    )

    outcome, after = repaired(document, version)

    assert after.passed
    contact = outcome.document.contact
    assert contact.full_name == "ALEX MORGAN"
    assert contact.phone == "+44 7700 900123"


def test_a_link_the_profile_does_not_hold_is_removed(version):
    document = a_document(
        contact=TailoredContact(
            full_name="ALEX MORGAN",
            links=[
                TailoredLink(url="https://github.com/alex", label="GitHub"),
                TailoredLink(url="https://linkedin.com/in/someone-else", label="LinkedIn"),
            ],
        )
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert [link.url for link in outcome.document.contact.links] == [
        "https://github.com/alex"
    ]
    assert REASON_REMOVED_LINK in reasons(outcome)


def test_a_contradicted_link_label_is_restored_and_the_link_kept(version):
    document = a_document(
        contact=TailoredContact(
            full_name="ALEX MORGAN",
            links=[TailoredLink(url="https://github.com/alex", label="Portfolio")],
        )
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert outcome.document.contact.links[0].label == "GitHub"


# --- the limits --------------------------------------------------------------------


def test_a_document_with_nothing_groundable_left_is_not_rescued(version):
    """Repair is not a way to always produce a document.

    Every entry here is invented, so every entry is removed, and what is left is
    a resume with no career in it — which grounding rejects on the next pass.
    The run completes and withholds the artifact, exactly as it did before this
    module existed.
    """
    document = a_document(
        experience=[
            TailoredExperience(
                source_id=998,
                company="Nowhere Ltd",
                job_title="CTO",
                start_date=date(2018, 1, 1),
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert not after.passed
    assert any(
        finding.reason == grounding.REASON_EMPTY_DOCUMENT for finding in after.errors
    )


def test_a_document_that_already_passes_is_returned_untouched(version):
    document = a_document(
        experience=[an_entry(bullets=[a_bullet("Built the payments ingestion service in Python.")])]
    )
    report = grounding.validate(document, version)
    assert report.passed

    outcome = reconciliation.repair(document, version, report)

    assert outcome.document is document
    assert outcome.repairs == ()
    assert outcome.changed is False


def test_warnings_are_never_acted_on(version):
    """A reworded bullet is a WARNING: the facts are the profile's, the phrasing
    is new. That is the contribution a model is here for, and removing it would
    leave the model with nothing to do but reorder sentences."""
    document = a_document(
        experience=[
            an_entry(
                bullets=[
                    a_bullet("Delivered the payments ingestion service in Python."),
                    a_bullet("Led the migration to Kubernetes.", quote="Led the migration to Kubernetes."),
                ]
            )
        ]
    )

    outcome, after = repaired(document, version)

    assert after.passed
    assert [line.text for line in outcome.document.experience[0].bullets] == [
        "Delivered the payments ingestion service in Python."
    ]


def test_every_error_grounding_can_raise_is_one_repair_understands(version):
    """The guard on the address parser.

    `reconciliation` reads `Finding.field_path` — a string built from format
    strings in `grounding`. A check added there with a new path shape would be
    silently unrepairable, and the only symptom would be AI documents failing
    more often than they need to. This document carries one of every error the
    validator raises except the empty-document one, which has no repair by
    design, and asserts every single one was understood. The career-integrity
    and duplicate errors added later are covered the same way by
    `test_tailoring_grounding_hardening.test_every_new_error_is_one_repair_understands`.
    """
    document = TailoredResumeDocument(
        contact=TailoredContact(
            full_name="Alexander Morgan",
            email="alex@example.com",
            phone="+44 (0) 7700 900 123",
            location="Bristol",
            links=[
                TailoredLink(url="https://example.com/not-mine", label="Blog"),
                TailoredLink(url="https://github.com/alex", label="Code"),
            ],
        ),
        headline=TailoredLine(
            text="Principal Architect", source_quote="Principal Architect", source_field="job_title"
        ),
        summary=[
            TailoredLine(
                text="Ten years of leadership.",
                source_quote="Ten years of leadership.",
                source_field="summary",
            )
        ],
        skills=[TailoredSkillGroup(category="Backend", skills=["Python", "Rust"])],
        experience=[
            an_entry(
                company="Acme Corporation Ltd",
                job_title="Staff Engineer",
                location="Munich",
                start_date=date(2021, 4, 1),
                start_date_precision="year",
                end_date=date(2024, 7, 1),
                end_date_precision="day",
                bullets=[
                    a_bullet("Mentored 14 new engineers."),
                    a_bullet("Led the migration to Kubernetes.", quote="Led the migration to Kubernetes."),
                ],
            ),
            TailoredExperience(
                source_id=999,
                company="Somewhere Else",
                job_title="VP Engineering",
                start_date=date(2015, 1, 1),
            ),
        ],
        projects=[
            TailoredProject(
                source_id=201,
                name="EasyApply Pro",
                role="Author",
                technologies=["FastAPI", "Kafka"],
                bullets=[a_bullet("Handled 4m ledger entries.", quote=PROJECT_DESCRIPTION)],
            ),
            TailoredProject(source_id=888, name="Ghost Project"),
        ],
        education=[
            TailoredEducation(
                source_id=401,
                institution="State College",
                degree="MSc",
                field_of_study="Mathematics",
                start_date=date(2015, 1, 1),
                start_date_precision="month",
            ),
            TailoredEducation(source_id=777, institution="Imaginary University"),
        ],
        certifications=[
            TailoredCertification(source_id=501, name="AZ-901", issuing_organization="Contoso"),
            TailoredCertification(source_id=666, name="Invented Credential"),
        ],
    )

    report = grounding.validate(document, version)
    assert len(report.errors) > 15, "this document is meant to break everything"

    outcome = reconciliation.repair(document, version, report)

    assert outcome.unrepairable == (), [
        (finding.field_path, finding.reason) for finding in outcome.unrepairable
    ]


def test_a_repair_can_only_ever_remove(version):
    """The property the whole module rests on.

    Every line, skill, technology and entry in the repaired document was in the
    document the generator produced. The only values that may differ are the
    transcribed fields, and those may only differ by being EQUAL TO THE SNAPSHOT
    — which is the one edit that cannot introduce a claim.
    """
    document = TailoredResumeDocument(
        contact=TailoredContact(
            full_name="Alexander Morgan",
            phone="+44 (0) 7700 900 123",
            links=[
                TailoredLink(url="https://example.com/not-mine", label="Blog"),
                TailoredLink(url="https://github.com/alex", label="Code"),
            ],
        ),
        summary=[
            TailoredLine(
                text="Backend engineer focused on payments and data pipelines.",
                source_quote="Backend engineer focused on payments and data pipelines.",
                source_field="summary",
            ),
            TailoredLine(
                text="Ten years of leadership.",
                source_quote="Ten years of leadership.",
                source_field="summary",
            ),
        ],
        skills=[TailoredSkillGroup(category="Backend", skills=["Python", "Rust"])],
        experience=[
            an_entry(
                company="Acme Corporation Ltd",
                bullets=[
                    a_bullet("Mentored 14 new engineers."),
                    a_bullet("Built the payments ingestion service in Python."),
                ],
            ),
            TailoredExperience(
                source_id=999, company="Somewhere Else", job_title="VP", start_date=date(2015, 1, 1)
            ),
        ],
        projects=[
            TailoredProject(source_id=201, name="EasyApply", technologies=["FastAPI", "Kafka"])
        ],
    )

    report = grounding.validate(document, version)
    outcome = reconciliation.repair(document, version, report)
    after = outcome.document

    def lines(doc):
        out = [line.text for line in doc.summary]
        if doc.headline:
            out.append(doc.headline.text)
        for entry in list(doc.experience) + list(doc.projects):
            out += [bullet.text for bullet in entry.bullets]
        return out

    assert set(lines(after)) <= set(lines(document))
    assert len(lines(after)) < len(lines(document)), "this case is meant to remove something"

    original_skills = {name for group in document.skills for name in group.skills}
    assert {name for group in after.skills for name in group.skills} <= original_skills

    assert {project.source_id for project in after.projects} <= {
        project.source_id for project in document.projects
    }
    assert {entry.source_id for entry in after.experience} <= {
        entry.source_id for entry in document.experience
    }
    assert set(after.projects[0].technologies) <= set(document.projects[0].technologies)
    assert {link.url for link in after.contact.links} <= {
        link.url for link in document.contact.links
    }

    # Every transcribed field that changed changed INTO the snapshot's value.
    stored = version.experiences[0]
    kept = next(entry for entry in after.experience if entry.source_id == 101)
    assert kept.company == stored.company
    assert after.contact.full_name == version.full_name
    assert after.contact.phone == version.phone
