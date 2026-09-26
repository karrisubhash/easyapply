"""Grounding hardening: the lies the first validator let through.

Every case here was reproduced against the validator as it stood, by an offline
probe, before the check that now catches it was written. They are grouped by
the audit finding they answer (C1, C2, ...), and each group opens with the
attack and then pins the repair, because a check the repair pass cannot act on
turns a model's one bad line back into a withheld document.

Two properties are asserted for every new rule rather than argued:

  * the deterministic generator's document for every `tailoring_v1` case still
    passes with no errors (`test_tailoring_evaluation.py` holds that per case;
    the rules that could plausibly fire on it are pinned here as well), and
  * a repair only ever removes, restores an exact profile value, or reorders
    entries — it never writes a word the profile does not hold.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.tailoring import SEVERITY_ERROR, SEVERITY_WARNING
from app.schemas.tailored_resume import (
    TailoredContact,
    TailoredExperience,
    TailoredLine,
    TailoredProject,
    TailoredResumeDocument,
    TailoredSkillGroup,
)
from app.services.tailoring import grounding, reconciliation
from tests.matching_fixtures import a_project_row, a_skill_row, a_version, an_experience_row

NORTHWIND = (
    "Rebuilt the shipment tracking service in Python and FastAPI, cutting p99 latency by 63%. "
    "Mentored 4 engineers through design review and on-call onboarding. "
    "Moved the team onto PostgreSQL."
)
CAVENDISH = "Built HL7 ingestion pipelines processing 250k patient records daily."
LEDGER = "Reconciles 40k ledger entries nightly against the bank feed."


def a_skill(name: str, category: str | None = None, *, id: int):
    row = a_skill_row(name, id=id)
    row.category = category
    return row


def _version():
    version = a_version(
        full_name="ALEX MORGAN",
        email="alex@example.com",
        phone="+44 7700 900123",
        location="Bristol, United Kingdom",
        summary="Backend engineer focused on distributed systems and data pipelines.",
        experiences=[
            an_experience_row(
                company="Northwind Logistics",
                job_title="Senior Backend Engineer",
                location="Bristol",
                start=date(2022, 3, 1),
                end=None,
                description=NORTHWIND,
                id=101,
            ),
            an_experience_row(
                company="Cavendish Health",
                job_title="Backend Engineer",
                location="Reading",
                start=date(2019, 7, 1),
                end=date(2022, 2, 1),
                description=CAVENDISH,
                id=102,
            ),
        ],
        projects=[
            a_project_row(
                name="Ledger Engine 2",
                technologies=["Python", "Celery"],
                description=LEDGER,
                id=201,
            )
        ],
        skills=[
            a_skill("Python", "Languages", id=301),
            a_skill("Go", "Languages", id=302),
            a_skill("Kafka", "Data", id=303),
            a_skill("Terraform", id=304),
        ],
    )
    for row in version.experiences:
        row.start_date_precision = "month"
        row.end_date_precision = "month" if row.end_date else None
    return version


@pytest.fixture
def version():
    return _version()


def line(text: str, quote: str, field: str = "description") -> TailoredLine:
    return TailoredLine(text=text, source_quote=quote, source_field=field)


def northwind(bullets=None, **overrides) -> TailoredExperience:
    base = dict(
        source_id=101,
        company="Northwind Logistics",
        job_title="Senior Backend Engineer",
        location="Bristol",
        start_date=date(2022, 3, 1),
        start_date_precision="month",
        bullets=bullets
        if bullets is not None
        else [line("Moved the team onto PostgreSQL.", "Moved the team onto PostgreSQL.")],
    )
    base.update(overrides)
    return TailoredExperience(**base)


def cavendish(bullets=None, **overrides) -> TailoredExperience:
    base = dict(
        source_id=102,
        company="Cavendish Health",
        job_title="Backend Engineer",
        location="Reading",
        start_date=date(2019, 7, 1),
        start_date_precision="month",
        end_date=date(2022, 2, 1),
        end_date_precision="month",
        bullets=bullets if bullets is not None else [line(CAVENDISH, CAVENDISH)],
    )
    base.update(overrides)
    return TailoredExperience(**base)


def a_contact(**overrides) -> TailoredContact:
    base = dict(
        full_name="ALEX MORGAN",
        email="alex@example.com",
        phone="+44 7700 900123",
        location="Bristol, United Kingdom",
    )
    base.update(overrides)
    return TailoredContact(**base)


def a_document(**overrides) -> TailoredResumeDocument:
    base = dict(contact=a_contact(), experience=[northwind(), cavendish()])
    base.update(overrides)
    return TailoredResumeDocument(**base)


def reasons(report) -> list[str]:
    return [finding.reason for finding in report.findings]


def error_reasons(report) -> list[str]:
    return [finding.reason for finding in report.errors]


def settle(document, version):
    return reconciliation.settle(document, version, repair_allowed=True)


def test_the_fixture_document_is_clean(version):
    """Every attack below is a change to this document, so it has to pass."""
    report = grounding.validate(a_document(), version)
    assert report.errors == (), [(f.field_path, f.reason) for f in report.errors]


# --- C1: a number is licensed by its own quote, not by the whole row ------------------


def test_a_number_from_elsewhere_in_the_same_description_is_invented(version):
    """"63" is in Northwind's description — as a latency percentage. It is not in
    the sentence this bullet cites, which counted four engineers. Supporting the
    line with the whole description let it borrow any number the job ever
    stated and attach it to any claim."""
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line(
                        "Mentored 63 engineers through design review and on-call onboarding.",
                        "Mentored 4 engineers through design review and on-call onboarding.",
                    )
                ]
            ),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_INVENTED_NUMBER in error_reasons(report)


def test_a_number_in_its_own_quote_is_still_fine(version):
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line(
                        "Mentored 4 engineers on design review and on-call.",
                        "Mentored 4 engineers through design review and on-call onboarding.",
                    )
                ]
            ),
            cavendish(),
        ]
    )

    assert grounding.validate(document, version).passed


def test_a_number_in_the_rows_identity_fields_is_licensed(version):
    """The project is called "Ledger Engine 2". A bullet on it may say so."""
    document = a_document(
        projects=[
            TailoredProject(
                source_id=201,
                name="Ledger Engine 2",
                technologies=["Python"],
                bullets=[line("Ledger Engine 2 reconciles ledger entries nightly.", LEDGER)],
            )
        ]
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_NUMBER not in reasons(report)


def test_a_project_number_from_the_rest_of_its_description_is_invented(version):
    version.projects[0].description = LEDGER + " Cut reconciliation time by 90%."
    document = a_document(
        projects=[
            TailoredProject(
                source_id=201,
                name="Ledger Engine 2",
                technologies=["Python"],
                bullets=[
                    line(
                        "Reconciles 40k ledger entries nightly, 90% faster, against the bank feed.",
                        LEDGER,
                    )
                ],
            )
        ]
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_NUMBER in error_reasons(report)


def test_a_name_from_the_same_rows_description_is_still_silent(version):
    """Names keep row-level support: PostgreSQL was used at THIS job. That is a
    reader's call, not a contradiction, and it is not what C1 was about."""
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line(
                        "Mentored 4 engineers through design review, on-call and PostgreSQL.",
                        "Mentored 4 engineers through design review and on-call onboarding.",
                    )
                ]
            ),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    assert report.passed
    assert grounding.REASON_BORROWED_NAME not in reasons(report)


def test_an_invented_number_is_repaired_by_removing_the_line(version):
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line(
                        "Mentored 63 engineers through design review and on-call onboarding.",
                        "Mentored 4 engineers through design review and on-call onboarding.",
                    ),
                    line("Moved the team onto PostgreSQL.", "Moved the team onto PostgreSQL."),
                ]
            ),
            cavendish(),
        ]
    )

    settled = settle(document, version)

    assert settled.report.passed
    assert [b.text for b in settled.document.experience[0].bullets] == [
        "Moved the team onto PostgreSQL."
    ]


# --- C2: a skills heading is a profile category, the lead group, or "Other" ------------


def test_the_lead_heading_is_the_generators_own_constant():
    """Grounding mirrors the constant rather than importing the generator, so
    that it keeps depending on nothing that ranks. This is what stops the two
    drifting apart."""
    from app.services.tailoring.generation import LEAD_SKILL_GROUP

    assert grounding.LEAD_SKILL_GROUP == LEAD_SKILL_GROUP
    assert grounding.UNCATEGORISED_SKILL_GROUP == "Other"


@pytest.mark.parametrize(
    "heading", ["Expert, 10+ years, ex-Google", "Core Skills", "Cloud-native expertise", ""]
)
def test_a_skills_heading_that_is_not_a_category_is_an_error(version, heading):
    """The heading is printed in bold in the DOCX. "Expert, 10+ years, ex-Google"
    is three fabricated claims wearing the clothes of a layout decision. An empty
    heading claims nothing but prints as ": Python"; refiling it under the
    stored categories is the repair, so it is refused too."""
    document = a_document(skills=[TailoredSkillGroup(category=heading, skills=["Python"])])

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_UNKNOWN_SKILL_GROUP in error_reasons(report)


@pytest.mark.parametrize(
    "heading",
    [
        "Relevant to this role",
        "relevant to  this ROLE",
        "Languages",
        "languages",
        "Other",
        # neutral headings the run2/run3 models wrote; they claim nothing
        "Additional",
        "Other skills",
        "Additional Skills",
    ],
)
def test_the_headings_a_profile_supports_pass(version, heading):
    document = a_document(skills=[TailoredSkillGroup(category=heading, skills=["Python"])])

    report = grounding.validate(document, version)

    assert report.passed, [(f.field_path, f.reason) for f in report.errors]


def test_an_invented_heading_is_regrouped_under_the_stored_categories(version):
    """The repair reorganises profile values and writes no word of its own:
    each skill goes back under the category the profile files it under, merged
    into a group of that heading when the document already has one; an
    uncategorised skill goes under "Other"."""
    document = a_document(
        skills=[
            TailoredSkillGroup(category="Relevant to this role", skills=["Kafka"]),
            TailoredSkillGroup(
                category="Expert, 10+ years, ex-Google", skills=["Python", "Terraform", "Celery"]
            ),
            TailoredSkillGroup(category="Languages", skills=["Go"]),
        ]
    )

    settled = settle(document, version)

    assert settled.report.passed, [(f.field_path, f.reason) for f in settled.report.errors]
    assert [(g.category, g.skills) for g in settled.document.skills] == [
        ("Relevant to this role", ["Kafka"]),
        ("Other", ["Terraform", "Celery"]),
        ("Languages", ["Go", "Python"]),
    ]
    headings = {g.category for g in settled.document.skills}
    assert "Expert, 10+ years, ex-Google" not in headings
    assert reconciliation.REASON_RESTORED_FIELD in [r.reason for r in settled.repairs]


def test_regrouping_and_removing_an_unlisted_skill_compose(version):
    document = a_document(
        skills=[TailoredSkillGroup(category="Core Skills", skills=["Python", "Rust"])]
    )

    settled = settle(document, version)

    assert settled.report.passed
    assert [(g.category, g.skills) for g in settled.document.skills] == [("Languages", ["Python"])]


# --- C3a: a number spelled in words is still a number ----------------------------------

SUMMARY = "Backend engineer focused on distributed systems and data pipelines."


def summary_line(text: str, quote: str = SUMMARY) -> TailoredLine:
    return TailoredLine(text=text, source_quote=quote, source_field="summary")


@pytest.mark.parametrize(
    "text",
    [
        "Ten years as a backend engineer focused on distributed systems.",
        "Backend engineer who managed twelve engineers on distributed systems.",
        "Backend engineer with a decade of distributed systems work.",
        "Backend engineer who doubled throughput on data pipelines.",
        "Backend engineer focused on distributed systems for twenty-five teams.",
        "Backend engineer who cut pipeline costs in half.",
    ],
)
def test_a_number_word_the_quote_does_not_state_is_invented(version, text):
    """"Ten years", "twelve engineers", "a decade", "doubled" - each is a quantity
    an interviewer can ask about, and each used to pass as a REWORDED warning
    whose text told the user the facts were in their profile."""
    report = grounding.validate(a_document(summary=[summary_line(text)]), version)

    assert not report.passed
    assert grounding.REASON_INVENTED_NUMBER in error_reasons(report)


def test_a_number_word_in_its_own_quote_is_fine():
    version = a_version(
        experiences=[
            an_experience_row(
                company="Acme", job_title="Engineer", start=date(2020, 1, 1), end=None,
                description="Mentored two new engineers through onboarding.", id=101,
            )
        ],
    )
    version.experiences[0].start_date_precision = "month"
    document = TailoredResumeDocument(
        experience=[
            TailoredExperience(
                source_id=101, company="Acme", job_title="Engineer",
                start_date=date(2020, 1, 1), start_date_precision="month",
                bullets=[
                    line(
                        "Onboarded two new engineers as their mentor.",
                        "Mentored two new engineers through onboarding.",
                    )
                ],
            )
        ]
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_NUMBER not in reasons(report)


def test_one_is_not_treated_as_a_number(version):
    """"one of the", "no one", "one-off": the word is a determiner far more often
    than a count, and a claim of ONE is never an inflation."""
    report = grounding.validate(
        a_document(
            summary=[summary_line("Backend engineer, one focused on distributed systems.")]
        ),
        version,
    )

    assert grounding.REASON_INVENTED_NUMBER not in reasons(report)


# --- C3b: Unicode is text too ------------------------------------------------------------


def test_full_width_digits_are_digits(version):
    report = grounding.validate(
        a_document(
            summary=[summary_line("Backend engineer focused on １２ distributed systems.")]
        ),
        version,
    )

    assert not report.passed
    assert grounding.REASON_INVENTED_NUMBER in error_reasons(report)


def test_a_non_ascii_proper_noun_is_seen(version):
    """"Ørsted" was invisible to an ASCII tokeniser: "rsted" is not capitalised."""
    report = grounding.validate(
        a_document(
            summary=[summary_line("Backend engineer focused on distributed systems at Ørsted.")]
        ),
        version,
    )

    assert not report.passed
    assert grounding.REASON_INVENTED_NAME in error_reasons(report)


def test_text_in_a_caseless_script_the_profile_does_not_hold_is_invented(version):
    """No capitalisation to read a name from, so nothing to excuse it with: a
    token the profile does not contain is treated as the invention it may be."""
    report = grounding.validate(
        a_document(
            summary=[
                summary_line(
                    "Backend engineer focused on 分散システム at 谷歌."
                )
            ]
        ),
        version,
    )

    assert not report.passed
    assert grounding.REASON_INVENTED_NAME in error_reasons(report)


def test_caseless_text_the_profile_does_hold_is_not_invented():
    backend = "バックエンド"
    engineer = "エンジニア"
    design = "設計"
    version = a_version(
        summary=f"{backend} {engineer}",
        experiences=[an_experience_row(description=f"分散 {design}", id=101)],
    )
    document = TailoredResumeDocument(
        summary=[
            TailoredLine(
                text=f"{backend} {engineer} {design}",
                source_quote=f"{backend} {engineer}",
                source_field="summary",
            )
        ],
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_NAME not in reasons(report)


# --- C3c: a technology is a name in any case, at any position ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Moved the team onto PostgreSQL, in rust and FastAPI.",
        "Rust mentoring: moved the team onto PostgreSQL.",
        "Moved the team onto postgresql and kubernetes.",
    ],
)
def test_a_technology_the_profile_never_names_is_invented_in_any_case(version, text):
    document = a_document(
        experience=[northwind(bullets=[line(text, "Moved the team onto PostgreSQL.")]), cavendish()]
    )

    report = grounding.validate(document, version)

    assert not report.passed
    assert grounding.REASON_INVENTED_NAME in error_reasons(report)


def test_a_lowercase_technology_from_elsewhere_in_the_profile_is_borrowed(version):
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line("Moved the team onto PostgreSQL and kafka.", "Moved the team onto PostgreSQL.")
                ]
            ),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    assert report.passed
    assert grounding.REASON_BORROWED_NAME in reasons(report)


@pytest.mark.parametrize("word", ["react", "rest", "go", "spring", "express", "less", "windows"])
def test_a_technology_that_is_also_an_english_word_keeps_the_old_rule(version, word):
    """Lowercase "react to incidents", "the rest of the team", "go live" are
    prose. Those keep the capitalised-mid-line heuristic and nothing more."""
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line(
                        f"Moved the team onto PostgreSQL, {word} included.",
                        "Moved the team onto PostgreSQL.",
                    )
                ]
            ),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_NAME not in reasons(report)


def test_the_english_word_exclusions_are_all_real_vocabulary_entries():
    """A typo in the exclusion list would silently exclude nothing."""
    from app.services.matching.vocabulary import TECHNOLOGIES

    assert grounding._TECHNOLOGIES_THAT_ARE_ENGLISH_WORDS <= TECHNOLOGIES
    assert {"rust", "kafka", "kubernetes", "python"} <= grounding._TECHNOLOGY_NAMES
    assert not grounding._TECHNOLOGIES_THAT_ARE_ENGLISH_WORDS & grounding._TECHNOLOGY_NAMES


# --- C3d: the headline is a copied field ---------------------------------------------------


def a_headline(text: str, quote: str | None = None) -> TailoredLine:
    return TailoredLine(text=text, source_quote=quote or text, source_field="job_title")


@pytest.mark.parametrize(
    "text, quote",
    [
        ("Principal Backend Engineer", "Senior Backend Engineer"),
        ("Staff Engineer", "Staff Engineer"),
        ("Backend Engineering Lead", "Backend Engineer"),
    ],
)
def test_a_headline_that_is_not_a_stored_job_title_is_an_error(version, text, quote):
    report = grounding.validate(a_document(headline=a_headline(text, quote)), version)

    assert not report.passed
    assert grounding.REASON_HEADLINE_NOT_A_TITLE in error_reasons(report)


def test_a_stored_title_in_any_case_is_a_valid_headline(version):
    report = grounding.validate(
        a_document(headline=a_headline("senior backend  ENGINEER", "Senior Backend Engineer")),
        version,
    )

    assert report.passed, [(f.field_path, f.reason) for f in report.errors]


def test_a_promoted_headline_is_restored_from_its_own_quote(version):
    """The quote names a title the profile holds, so the snapshot has the right
    answer: restore it, exactly, cited to the column it lives in."""
    document = a_document(
        headline=a_headline("Principal Backend Engineer", "Senior Backend Engineer")
    )

    settled = settle(document, version)

    assert settled.report.passed
    assert settled.document.headline == TailoredLine(
        text="Senior Backend Engineer",
        source_quote="Senior Backend Engineer",
        source_field="job_title",
    )
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_RESTORED_FIELD]


def test_an_invented_headline_with_no_title_to_restore_is_removed(version):
    settled = settle(a_document(headline=a_headline("Staff Engineer")), version)

    assert settled.report.passed
    assert settled.document.headline is None
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_REMOVED_LINE]


# --- C3e: a seniority the line's own source does not give ----------------------------------

MOVED = "Moved the team onto PostgreSQL."


@pytest.mark.parametrize(
    "entry, text, quote",
    [
        ("northwind", "As principal engineer, moved the team onto PostgreSQL.", MOVED),
        ("northwind", "Served as tech lead moving the team onto PostgreSQL.", MOVED),
        ("northwind", "Staff Software Engineer who moved the team onto PostgreSQL.", MOVED),
        ("northwind", "Head of the move onto PostgreSQL as engineering manager.", MOVED),
        ("cavendish", "As senior engineer, built HL7 ingestion pipelines.", CAVENDISH),
        ("cavendish", "Director-level owner of HL7 ingestion pipelines.", CAVENDISH),
    ],
)
def test_a_seniority_the_source_does_not_state_is_an_error(version, entry, text, quote):
    bullets = [line(text, quote)]
    experience = (
        [northwind(bullets=bullets), cavendish()]
        if entry == "northwind"
        else [northwind(), cavendish(bullets=bullets)]
    )

    report = grounding.validate(a_document(experience=experience), version)

    assert not report.passed
    assert grounding.REASON_INVENTED_SENIORITY in error_reasons(report)


@pytest.mark.parametrize(
    "text",
    [
        # the row's own title licenses its words
        "As a senior engineer, moved the team onto PostgreSQL.",
        # verbs are not titles
        "Led the team onto PostgreSQL.",
        "Lead the team onto PostgreSQL.",
        "Managed the team's move onto PostgreSQL.",
    ],
)
def test_verbs_and_the_rows_own_title_are_not_fabricated_seniority(version, text):
    document = a_document(experience=[northwind(bullets=[line(text, MOVED)]), cavendish()])

    report = grounding.validate(document, version)

    assert grounding.REASON_INVENTED_SENIORITY not in reasons(report)


def test_a_summary_may_use_a_title_the_candidate_holds(version):
    report = grounding.validate(
        a_document(
            summary=[summary_line("Senior backend engineer focused on distributed systems.")]
        ),
        version,
    )

    assert grounding.REASON_INVENTED_SENIORITY not in reasons(report)


def test_a_summary_may_not_use_a_title_the_candidate_never_held(version):
    report = grounding.validate(
        a_document(
            summary=[summary_line("Principal backend engineer focused on distributed systems.")]
        ),
        version,
    )

    assert grounding.REASON_INVENTED_SENIORITY in error_reasons(report)


def test_fabricated_seniority_is_repaired_by_removing_the_line(version):
    document = a_document(
        experience=[
            northwind(),
            cavendish(
                bullets=[
                    line("As senior engineer, built HL7 ingestion pipelines.", CAVENDISH),
                    line(CAVENDISH, CAVENDISH),
                ]
            ),
        ]
    )

    settled = settle(document, version)

    assert settled.report.passed
    assert [b.text for b in settled.document.experience[1].bullets] == [CAVENDISH]


# --- H2: the career is whole, unrepeated and in order --------------------------------------


def ids(document) -> list[int]:
    return [entry.source_id for entry in document.experience]


def test_swapped_experience_ids_do_not_settle_into_a_broken_career(version):
    """The audit case. Each job carried the OTHER job's id, so repair restored
    every identity field from the wrong row, removed every bullet as a citation
    of the wrong description, and delivered two bulletless jobs in reverse
    order as "passed"."""
    swapped = a_document(
        experience=[
            northwind().model_copy(update={"source_id": 102}),
            cavendish().model_copy(update={"source_id": 101}),
        ]
    )

    settled = settle(swapped, version)

    assert not settled.report.passed
    assert settled.document is swapped, "a repair that does not deliver is not applied"
    assert grounding.REASON_EXPERIENCE_OUT_OF_ORDER in error_reasons(settled.report)


def test_a_second_job_claiming_the_first_jobs_id_is_not_delivered(version):
    """Northwind twice and Cavendish nowhere: the duplicate can be removed, but
    the job it displaced cannot be conjured back, so the document fails."""
    document = a_document(
        experience=[northwind(), cavendish().model_copy(update={"source_id": 101})]
    )

    report = grounding.validate(document, version)
    assert grounding.REASON_DUPLICATE_ENTRY in error_reasons(report)
    assert grounding.REASON_MISSING_EXPERIENCE in error_reasons(report)

    settled = settle(document, version)
    assert not settled.report.passed


def test_a_duplicated_job_is_removed_and_the_first_kept(version):
    document = a_document(experience=[northwind(), northwind(), cavendish()])

    report = grounding.validate(document, version)
    assert [f.field_path for f in report.errors] == ["experience[1]"]
    assert report.errors[0].reason == grounding.REASON_DUPLICATE_ENTRY

    settled = settle(document, version)
    assert settled.report.passed
    assert ids(settled.document) == [101, 102]
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_REMOVED_DUPLICATE]


@pytest.mark.parametrize("section", ["projects", "education", "certifications"])
def test_a_duplicated_entry_of_any_kind_is_removed(section):
    from app.schemas.tailored_resume import TailoredCertification, TailoredEducation
    from tests.matching_fixtures import a_certification_row, an_education_row

    version = _version()
    version.education = [an_education_row(institution="State University", id=401)]
    version.certifications = [a_certification_row("AZ-900", id=501)]
    entry = {
        "projects": TailoredProject(source_id=201, name="Ledger Engine 2"),
        "education": TailoredEducation(
            source_id=401, institution="State University",
            degree=version.education[0].degree,
            field_of_study=version.education[0].field_of_study,
        ),
        "certifications": TailoredCertification(source_id=501, name="AZ-900"),
    }[section]
    document = a_document(**{section: [entry, entry]})

    report = grounding.validate(document, version)
    assert [(f.field_path, f.reason) for f in report.errors] == [
        (f"{section}[1]", grounding.REASON_DUPLICATE_ENTRY)
    ]

    settled = settle(document, version)
    assert settled.report.passed
    assert len(getattr(settled.document, section)) == 1


def test_an_omitted_job_is_an_error_no_repair_can_undo(version):
    """A missing job is a gap the reader asks about, and the repair pass cannot
    add one back: adding is the one thing it may never do."""
    document = a_document(experience=[northwind()])

    report = grounding.validate(document, version)
    assert grounding.REASON_MISSING_EXPERIENCE in error_reasons(report)

    outcome = reconciliation.repair(document, version, report)
    assert [f.reason for f in outcome.unrepairable] == [grounding.REASON_MISSING_EXPERIENCE]
    assert not settle(document, version).report.passed


def test_a_reordered_career_is_put_back_in_profile_order(version):
    """A permutation of the entries the document already has, into the order
    the profile holds them in. Nothing is added, nothing removed."""
    document = a_document(experience=[cavendish(), northwind()])

    report = grounding.validate(document, version)
    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("experience.order", grounding.REASON_EXPERIENCE_OUT_OF_ORDER)
    ]

    settled = settle(document, version)
    assert settled.report.passed
    assert ids(settled.document) == [101, 102]
    assert settled.document.experience == [northwind(), cavendish()]
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_RESTORED_FIELD]


def test_jobs_that_started_on_the_same_date_may_appear_in_either_order():
    """The profile orders by start date, so two jobs starting together have no
    order to be out of."""
    version = _version()
    version.experiences[1].start_date = version.experiences[0].start_date
    document = a_document(
        experience=[cavendish(start_date=date(2022, 3, 1)), northwind()]
    )

    report = grounding.validate(document, version)

    assert grounding.REASON_EXPERIENCE_OUT_OF_ORDER not in reasons(report)


def test_a_job_left_with_no_bullets_is_an_error_no_repair_can_undo(version):
    document = a_document(experience=[northwind(bullets=[]), cavendish()])

    report = grounding.validate(document, version)
    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("experience[0].bullets", grounding.REASON_NO_BULLETS)
    ]
    assert [f.reason for f in reconciliation.repair(document, version, report).unrepairable] == [
        grounding.REASON_NO_BULLETS
    ]


def test_a_repair_that_would_empty_a_job_is_not_applied(version):
    """Every bullet on Cavendish cites the wrong row. Removing them all would
    deliver a job with nothing under it, which reads as one being hidden; the
    document is withheld instead and the model's own text is what is stored."""
    document = a_document(
        experience=[northwind(), cavendish(bullets=[line("Moved the team onto PostgreSQL.", MOVED)])]
    )

    settled = settle(document, version)

    assert not settled.report.passed
    assert settled.document is document


def test_a_job_whose_row_holds_only_a_fragment_may_have_no_bullets():
    """"Quotable content" is defined by the generator's own clause splitter, so
    this rule can never fire on what the deterministic generator produced."""
    from app.services.tailoring.generation import DeterministicTailor, TailoringInputs
    from evaluation.datasets.tailoring_v1.inputs import cases, structured_job

    version = _version()
    version.experiences[1].description = "HL7 work."  # a fragment: under 12 characters
    case = cases()["cases"][0]
    job, requirements = structured_job(case["job"])
    generated = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document

    assert generated.experience[1].bullets == []
    report = grounding.validate(generated, version)
    assert report.errors == (), [(f.field_path, f.reason) for f in report.errors]


@pytest.fixture(scope="module")
def deterministic_documents():
    from app.services.tailoring.generation import (
        DeterministicTailor,
        TailoringInputs,
        requirement_labels,
    )
    from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job

    version = confirmed_version()
    out = []
    for case in cases()["cases"]:
        job, requirements = structured_job(case["job"])
        document = DeterministicTailor().generate(
            TailoringInputs(version=version, job=job, requirements=requirements)
        ).document
        out.append(
            (case["id"], document, version, frozenset(requirement_labels(requirements)))
        )
    return out


def test_no_new_rule_fires_on_a_deterministic_document(deterministic_documents):
    """The generator that copies verbatim must stay unable to fail grounding.
    Checked for EVERY finding, warnings included: a deterministic document
    whose text is its own quote has nothing any line rule could object to."""
    for case_id, document, version, labels in deterministic_documents:
        report = grounding.validate(document, version, job_requirement_labels=labels)
        assert report.findings == (), (case_id, [(f.field_path, f.reason) for f in report.findings])


# --- M1: a contact detail the profile holds may not be left off ------------------------


def test_a_missing_contact_block_is_an_error(version):
    """No name, no email, no phone: the resume nobody can reply to, which is the
    defect `TailoredContact` was added to end — and `contact=None` passed."""
    report = grounding.validate(a_document(contact=None), version)

    assert not report.passed
    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("contact", grounding.REASON_CONTACT_MISSING)
    ]


@pytest.mark.parametrize("name", ["full_name", "email", "phone", "location"])
def test_a_contact_detail_left_null_is_an_error(version, name):
    report = grounding.validate(a_document(contact=a_contact(**{name: None})), version)

    assert not report.passed
    assert [(f.field_path, f.reason) for f in report.errors] == [
        (f"contact.{name}", grounding.REASON_CONTACT_MISSING)
    ]


def test_an_all_null_contact_is_four_errors(version):
    blank = TailoredContact()

    report = grounding.validate(a_document(contact=blank), version)

    assert [f.field_path for f in report.errors] == [
        "contact.full_name", "contact.email", "contact.phone", "contact.location"
    ]


def test_a_detail_the_profile_does_not_hold_may_be_absent():
    version = _version()
    version.phone = None
    report = grounding.validate(a_document(contact=a_contact(phone=None)), version)

    assert report.passed, [(f.field_path, f.reason) for f in report.errors]


def test_a_version_with_no_contact_needs_no_contact_block():
    version = _version()
    version.full_name = version.email = version.phone = version.location = None

    report = grounding.validate(a_document(contact=None), version)

    assert report.passed, [(f.field_path, f.reason) for f in report.errors]


def test_a_left_out_detail_is_restored_from_the_profile(version):
    settled = settle(a_document(contact=a_contact(email=None, phone=None)), version)

    assert settled.report.passed
    assert settled.document.contact == a_contact()
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_RESTORED_FIELD] * 2


def test_a_missing_contact_block_is_restored_from_the_profile(version):
    """The four columns, exactly as stored. Links are not invented back: which of
    them belong in a header is a selection, and this module does not select."""
    settled = settle(a_document(contact=None), version)

    assert settled.report.passed
    assert settled.document.contact == a_contact()
    assert settled.document.contact.links == []
    assert [(r.field_path, r.reason) for r in settled.repairs] == [
        ("contact", reconciliation.REASON_RESTORED_FIELD)
    ]


# --- M2: nothing printed twice, nothing printed empty ----------------------------------

HL7 = line(CAVENDISH, CAVENDISH)


def test_a_sentence_printed_twice_is_an_error_on_the_later_copy(version):
    document = a_document(
        experience=[northwind(), cavendish(bullets=[HL7, line(" built  hl7 ingestion pipelines processing 250k patient records daily. ".strip(), CAVENDISH)])],
    )

    report = grounding.validate(document, version)

    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("experience[1].bullets[1]", grounding.REASON_DUPLICATE_LINE)
    ]


def test_a_summary_line_reprinted_as_a_bullet_is_an_error_on_the_summary(version):
    """The original quality defect: the summary was three of the bullets.

    The error sits on the SUMMARY copy. It used to sit on the bullet, and here
    the bullet is Cavendish's only one: removing it left the job bulletless,
    which no repair can fix, so a sendable document was withheld to save one
    summary sentence."""
    document = a_document(summary=[HL7], experience=[northwind(), cavendish(bullets=[HL7])])

    report = grounding.validate(document, version)

    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("summary[0]", grounding.REASON_DUPLICATE_LINE)
    ]


def test_repeating_a_jobs_only_bullet_in_the_summary_still_delivers_the_job(version):
    document = a_document(summary=[HL7], experience=[northwind(), cavendish(bullets=[HL7])])

    settled = settle(document, version)

    assert settled.report.passed
    assert settled.document.summary == []
    assert [b.text for b in settled.document.experience[1].bullets] == [HL7.text]


def test_a_copy_that_is_removed_anyway_does_not_cost_the_grounded_copy(version):
    """A summary line citing the wrong column (its quote is not in the summary)
    is removed for that; the identical, correctly cited bullet must stay."""
    broken = HL7.model_copy(update={"source_field": "summary"})
    document = a_document(summary=[broken], experience=[northwind(), cavendish(bullets=[HL7])])

    report = grounding.validate(document, version)
    assert ("experience[1].bullets[0]", grounding.REASON_DUPLICATE_LINE) not in {
        (f.field_path, f.reason) for f in report.errors
    }

    settled = settle(document, version)
    assert settled.report.passed
    assert [b.text for b in settled.document.experience[1].bullets] == [HL7.text]


def test_an_earlier_bullet_that_is_removed_anyway_does_not_cost_a_later_grounded_one(version):
    """The same sentence under Northwind (citing Northwind's description, where
    it is not) and under Cavendish (where it is). Northwind's copy goes for its
    own error; Cavendish's is the grounded copy and must not be taken as its
    duplicate."""
    misplaced = line(CAVENDISH, CAVENDISH)
    document = a_document(
        experience=[northwind(bullets=[line(MOVED, MOVED), misplaced]), cavendish(bullets=[HL7])]
    )

    report = grounding.validate(document, version)
    assert ("experience[1].bullets[0]", grounding.REASON_DUPLICATE_LINE) not in {
        (f.field_path, f.reason) for f in report.errors
    }


def test_a_repeated_line_is_removed_and_the_user_is_told_why(version):
    """Not "your profile does not support it" — it does. The repair says what
    actually happened."""
    document = a_document(
        experience=[northwind(), cavendish(bullets=[HL7, HL7])],
    )

    settled = settle(document, version)

    assert settled.report.passed
    assert settled.document.experience[1].bullets == [HL7]
    assert [(r.field_path, r.reason) for r in settled.repairs] == [
        ("experience[1].bullets[1]", reconciliation.REASON_REMOVED_DUPLICATE)
    ]


def test_a_skill_listed_twice_is_an_error_on_the_later_copy(version):
    document = a_document(
        skills=[
            TailoredSkillGroup(category="Relevant to this role", skills=["Python", "Kafka"]),
            TailoredSkillGroup(category="Languages", skills=["Go", "python"]),
        ]
    )

    report = grounding.validate(document, version)
    assert [(f.field_path, f.reason) for f in report.errors] == [
        ("skills[1].skills[1]", grounding.REASON_DUPLICATE_SKILL)
    ]

    settled = settle(document, version)
    assert settled.report.passed
    assert [(g.category, g.skills) for g in settled.document.skills] == [
        ("Relevant to this role", ["Python", "Kafka"]),
        ("Languages", ["Go"]),
    ]
    assert [r.reason for r in settled.repairs] == [reconciliation.REASON_REMOVED_DUPLICATE]


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_an_empty_line_is_an_error_and_is_removed(version, text):
    document = a_document(
        summary=[summary_line(text)],
        experience=[northwind(bullets=[line(MOVED, MOVED), line(text, MOVED)]), cavendish()],
    )

    report = grounding.validate(document, version)
    assert sorted((f.field_path, f.reason) for f in report.errors) == [
        ("experience[0].bullets[1]", grounding.REASON_EMPTY_LINE),
        ("summary[0]", grounding.REASON_EMPTY_LINE),
    ]

    settled = settle(document, version)
    assert settled.report.passed
    assert settled.document.summary == []
    assert [b.text for b in settled.document.experience[0].bullets] == [MOVED]


def test_the_deterministic_tailor_never_prints_a_sentence_twice():
    from app.services.tailoring.generation import DeterministicTailor, TailoringInputs
    from evaluation.datasets.tailoring_v1.inputs import cases, structured_job

    version = _version()
    shared = "Took part in the weekly on-call rotation for the platform."
    version.experiences[0].description += " " + shared
    version.experiences[1].description += " " + shared
    job, requirements = structured_job(cases()["cases"][0]["job"])
    document = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document

    report = grounding.validate(document, version)
    assert grounding.REASON_DUPLICATE_LINE not in reasons(report)


def test_a_job_whose_only_sentence_is_already_on_the_page_may_be_bulletless():
    """The two new rules meeting on one real shape: a sentence written verbatim
    under two jobs, and nothing else under the later one. The page prints it
    once, so the later job has nothing new to say — honestly bulletless, not a
    hollowed-out career — and the default generator's document still passes."""
    from app.services.tailoring.generation import DeterministicTailor, TailoringInputs
    from evaluation.datasets.tailoring_v1.inputs import cases, structured_job

    version = _version()
    shared = "Took part in the weekly on-call rotation for the platform."
    version.experiences[0].description += " " + shared
    version.experiences[1].description = shared
    job, requirements = structured_job(cases()["cases"][0]["job"])
    document = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document

    report = grounding.validate(document, version)
    assert report.passed, [(f.field_path, f.reason) for f in report.errors]
    assert [entry.company for entry in document.experience] == [
        "Northwind Logistics",
        "Cavendish Health",
    ]
    assert document.experience[1].bullets == []


def test_a_bulletless_job_with_something_new_to_say_is_still_an_error(version):
    """The narrowing above must not hide the original defect: a job whose own
    text is NOT already on the page, left empty, is still refused."""
    document = a_document(experience=[northwind(), cavendish(bullets=[])])
    report = grounding.validate(document, version)
    assert grounding.REASON_NO_BULLETS in reasons(report)


# --- L4: one finding per line per reason -------------------------------------------------


def test_a_line_with_several_reworded_words_is_one_warning(version):
    """Three new words used to be three identical warnings, and the review
    screen said "3 lines are worth a second look" about one line."""
    document = a_document(
        experience=[
            northwind(bullets=[line("Swiftly migrated the whole squad onto PostgreSQL.", MOVED)]),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    assert [(f.field_path, f.reason) for f in report.findings] == [
        ("experience[0].bullets[0]", grounding.REASON_REWORDED)
    ]


def test_distinct_reasons_on_one_line_are_each_reported_once(version):
    document = a_document(
        experience=[
            northwind(
                bullets=[
                    line("Moved 12 teams and 40 services onto PostgreSQL, Zorblax and Quuxly.", MOVED)
                ]
            ),
            cavendish(),
        ]
    )

    report = grounding.validate(document, version)

    # two numbers, two names, two reworded words ("teams", "services"): three
    # reasons, each once
    assert sorted(f.reason for f in report.findings) == sorted(
        [
            grounding.REASON_INVENTED_NUMBER,
            grounding.REASON_INVENTED_NAME,
            grounding.REASON_REWORDED,
        ]
    )


# --- L8: a finding always fits the row it is stored in ------------------------------------


def _column_lengths() -> dict[str, int]:
    from app.models.tailoring import TailoringIssue

    columns = TailoringIssue.__table__.c
    return {name: columns[name].type.length for name in ("section", "field_path", "claim", "reason")}


def _assert_fits(findings) -> None:
    lengths = _column_lengths()
    for finding in findings:
        for name, limit in lengths.items():
            value = getattr(finding, name)
            assert value is None or len(value) <= limit, (name, len(value), limit)


def test_an_overlong_skill_technology_and_label_still_fit_their_columns(version):
    """These reached the worker untruncated and raised DataError on insert — a
    stage that must never fail a run, failing it on the length of a string."""
    long = "Q" * 5000
    document = a_document(
        skills=[TailoredSkillGroup(category="Other", skills=["Python", long])],
        projects=[
            TailoredProject(source_id=201, name="Ledger Engine 2", technologies=["Python", long])
        ],
        targeted_requirements=[long],
    )

    report = grounding.validate(document, version, job_requirement_labels=frozenset({"Python"}))

    assert {f.reason for f in report.findings} >= {
        grounding.REASON_UNLISTED_SKILL,
        grounding.REASON_UNLISTED_TECHNOLOGY,
        grounding.REASON_UNTARGETED,
    }
    _assert_fits(report.findings)


def test_every_finding_is_truncated_at_construction():
    lengths = _column_lengths()
    finding = grounding.Finding(
        grounding.SEVERITY_ERROR,
        "s" * 100,
        "p" * 1000,
        "c" * 1000,
        "r" * 1000,
    )

    assert len(finding.section) == lengths["section"]
    assert len(finding.field_path) == lengths["field_path"]
    assert len(finding.claim) == lengths["claim"]
    assert len(finding.reason) == lengths["reason"]


def test_an_overlong_unlisted_skill_is_still_repaired(version):
    """Truncating the claim must not make the repair pass lose track of what to
    remove: it matches names under the same truncation."""
    long = "Q" * 5000
    document = a_document(skills=[TailoredSkillGroup(category="Other", skills=["Python", long])])

    settled = settle(document, version)

    assert settled.report.passed
    assert settled.document.skills[0].skills == ["Python"]
    _assert_fits(settled.repairs)


# --- the guards: every new error has a repair, and no repair writes a word ------------------

#: Errors with no repair BY DESIGN: each would need something added.
UNREPAIRABLE_BY_DESIGN = {
    grounding.REASON_EMPTY_DOCUMENT,
    grounding.REASON_MISSING_EXPERIENCE,
    grounding.REASON_NO_BULLETS,
}


def _everything_wrong() -> TailoredResumeDocument:
    """One of every new repairable error, on one document."""
    return TailoredResumeDocument(
        contact=a_contact(phone=None),
        headline=a_headline("Principal Backend Engineer", "Senior Backend Engineer"),
        summary=[
            summary_line(SUMMARY),
            summary_line("   "),
            summary_line("Ten years of work on distributed systems and data pipelines."),
        ],
        skills=[
            TailoredSkillGroup(category="Relevant to this role", skills=["Kafka", "Python"]),
            TailoredSkillGroup(category="Expert, 10+ years", skills=["Go", "Terraform"]),
            TailoredSkillGroup(category="Languages", skills=["python"]),
        ],
        experience=[
            cavendish(
                bullets=[
                    HL7,
                    HL7,
                    line("As senior engineer, built HL7 ingestion pipelines.", CAVENDISH),
                ]
            ),
            northwind(
                bullets=[
                    line(MOVED, MOVED),
                    line("Moved the team onto PostgreSQL and kubernetes.", MOVED),
                ]
            ),
            northwind(),
        ],
    )


def test_every_new_error_is_one_repair_understands(version):
    document = _everything_wrong()
    report = grounding.validate(document, version)

    raised = {finding.reason for finding in report.errors}
    assert raised >= {
        grounding.REASON_CONTACT_MISSING,
        grounding.REASON_HEADLINE_NOT_A_TITLE,
        grounding.REASON_EMPTY_LINE,
        grounding.REASON_INVENTED_NUMBER,
        grounding.REASON_UNKNOWN_SKILL_GROUP,
        grounding.REASON_DUPLICATE_SKILL,
        grounding.REASON_DUPLICATE_LINE,
        grounding.REASON_INVENTED_SENIORITY,
        grounding.REASON_INVENTED_NAME,
        grounding.REASON_EXPERIENCE_OUT_OF_ORDER,
        grounding.REASON_DUPLICATE_ENTRY,
    }, raised

    outcome = reconciliation.repair(document, version, report)
    assert outcome.unrepairable == (), [(f.field_path, f.reason) for f in outcome.unrepairable]

    settled = settle(document, version)
    assert settled.report.passed, [(f.field_path, f.reason) for f in settled.report.errors]


def test_the_only_unrepairable_errors_are_the_ones_that_would_need_an_addition(version):
    document = a_document(experience=[northwind(bullets=[])])  # Cavendish omitted
    report = grounding.validate(document, version)

    outcome = reconciliation.repair(document, version, report)

    assert {finding.reason for finding in outcome.unrepairable} <= UNREPAIRABLE_BY_DESIGN
    assert {finding.reason for finding in outcome.unrepairable} == {
        grounding.REASON_MISSING_EXPERIENCE,
        grounding.REASON_NO_BULLETS,
    }


def test_a_repair_still_only_removes_restores_or_reorders(version):
    """`test_a_repair_can_only_ever_remove`, extended to the new repairs.

    Every line in the repaired document was in the original, except a headline
    restored to a STORED job title. Every skill was in the original; every
    heading was in the original or is a stored category or "Other". The jobs
    are the original's, in the profile's order. Every contact value is the
    version's own.
    """
    document = _everything_wrong()
    after = settle(document, version).document

    def lines(doc):
        out = [line.text for line in doc.summary]
        for entry in list(doc.experience) + list(doc.projects):
            out += [bullet.text for bullet in entry.bullets]
        return out

    assert set(lines(after)) <= set(lines(document))
    assert after.headline.text in {row.job_title for row in version.experiences}

    before_skills = [name for group in document.skills for name in group.skills]
    after_skills = [name for group in after.skills for name in group.skills]
    assert all(after_skills.count(n) <= before_skills.count(n) for n in after_skills)
    allowed_headings = (
        {group.category for group in document.skills}
        | {skill.category for skill in version.skills if skill.category}
        | {grounding.UNCATEGORISED_SKILL_GROUP}
    )
    assert {group.category for group in after.skills} <= allowed_headings

    assert ids(after) == [row.id for row in version.experiences]
    for field_name in grounding.CONTACT_FIELDS:
        assert getattr(after.contact, field_name) == getattr(version, field_name)
