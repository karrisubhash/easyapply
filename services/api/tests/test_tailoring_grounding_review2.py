"""Grounding after the second review of the hardening pass (2026-09-26).

Every case here was reproduced offline before it was changed. Two kinds:

  * false positives — a faithful rewrite the hardened rules removed, which on
    the AI path costs a true line and, when it was a job's last bullet, the
    whole document: a number spelled another way ("four" for "4"), "no
    downtime" for "zero downtime", a technical name holding a rank word
    ("AWS Secrets Manager"), a summary copy of a job's only bullet;
  * holes — a claim the rules still let through: a line of punctuation only,
    and proficiency inflated through a stored category heading.

Each fix comes with the control beside it that must not move.
"""

from __future__ import annotations

import pytest

from app.models.skill import Skill
from app.services.tailoring import grounding
from app.services.tailoring.generation import DeterministicTailor, TailoringInputs
from app.services.tailoring.reconciliation import settle
from app.schemas.tailored_resume import TailoredSkillGroup
from tests.test_tailoring_grounding_hardening import (
    CAVENDISH,
    _version,
    a_document,
    cavendish,
    line,
    northwind,
    reasons,
)

MENTORED = "Mentored 4 engineers through design review and on-call onboarding."


@pytest.fixture
def version():
    return _version()


def _with_northwind_text(version, text: str):
    version.experiences[0].description = text
    return version


# --- numbers written another way ---------------------------------------------------


def test_a_number_word_for_the_quotes_own_digit_is_not_invented(version):
    document = a_document(
        experience=[
            northwind(bullets=[line("Mentored four engineers through design review and on-call onboarding.", MENTORED)]),
            cavendish(),
        ]
    )
    assert grounding.REASON_INVENTED_NUMBER not in reasons(grounding.validate(document, version))


def test_a_number_word_the_quote_does_not_state_is_still_invented(version):
    document = a_document(
        experience=[
            northwind(bullets=[line("Mentored twelve engineers through design review and on-call onboarding.", MENTORED)]),
            cavendish(),
        ]
    )
    assert grounding.REASON_INVENTED_NUMBER in reasons(grounding.validate(document, version))


def test_zero_downtime_reworded_is_neither_a_new_number_nor_a_negation(version):
    quote = "Delivered a zero-downtime migration to PostgreSQL."
    _with_northwind_text(version, quote)
    for text in (
        "Migrated to PostgreSQL with zero downtime.",
        "Migrated to PostgreSQL with no downtime.",
    ):
        document = a_document(experience=[northwind(bullets=[line(text, quote)]), cavendish()])
        found = reasons(grounding.validate(document, version))
        assert grounding.REASON_INVENTED_NUMBER not in found, text
        assert grounding.REASON_NEGATION_ADDED not in found, text


def test_a_multiplier_word_for_the_quotes_own_multiplier_is_not_invented(version):
    quote = "Improved ingest throughput 2x by batching writes."
    _with_northwind_text(version, quote)
    document = a_document(
        experience=[northwind(bullets=[line("Doubled ingest throughput by batching writes.", quote)]), cavendish()]
    )
    assert grounding.REASON_INVENTED_NUMBER not in reasons(grounding.validate(document, version))


def test_the_invented_number_reason_speaks_of_the_cited_text_not_the_profile():
    assert "text it cites" in grounding.REASON_INVENTED_NUMBER


# --- a negation the quote already carries --------------------------------------------


def test_a_negation_the_quote_already_carries_is_a_rewording_not_an_inversion(version):
    quote = "Rebuilt the billing service without downtime."
    _with_northwind_text(version, quote)
    document = a_document(
        experience=[northwind(bullets=[line("Rebuilt the billing service with no downtime.", quote)]), cavendish()]
    )
    assert grounding.REASON_NEGATION_ADDED not in reasons(grounding.validate(document, version))


def test_a_negation_the_quote_does_not_carry_is_still_an_inversion(version):
    quote = "Rebuilt the billing service in Python."
    _with_northwind_text(version, quote)
    document = a_document(
        experience=[northwind(bullets=[line("Did not rebuild the billing service in Python.", quote)]), cavendish()]
    )
    assert grounding.REASON_NEGATION_ADDED in reasons(grounding.validate(document, version))


# --- rank words inside technical names -----------------------------------------------


def test_a_technical_name_holding_a_rank_word_is_not_a_promotion(version):
    quote = "Rotated the database credentials weekly."
    _with_northwind_text(version, quote + " Stored credentials in AWS Secrets Manager.")
    document = a_document(
        experience=[
            northwind(bullets=[line("Rotated the database credentials weekly in AWS Secrets Manager.", quote)]),
            cavendish(),
        ]
    )
    assert grounding.REASON_INVENTED_SENIORITY not in reasons(grounding.validate(document, version))


def test_someone_elses_title_in_the_description_does_not_license_claiming_it(version):
    quote = "Rebuilt the billing service in Python."
    _with_northwind_text(version, quote + " Paired daily with the principal engineer.")
    document = a_document(
        experience=[
            northwind(bullets=[line("As principal engineer, rebuilt the billing service in Python.", quote)]),
            cavendish(),
        ]
    )
    assert grounding.REASON_INVENTED_SENIORITY in reasons(grounding.validate(document, version))


# --- punctuation is not a line -------------------------------------------------------


@pytest.mark.parametrize("text", ["—", "•", "...", " - "])
def test_a_line_of_punctuation_only_is_empty(version, text):
    document = a_document(experience=[northwind(bullets=[line(text, MENTORED)]), cavendish()])
    assert grounding.REASON_EMPTY_LINE in reasons(grounding.validate(document, version))


# --- proficiency through a stored heading --------------------------------------------


def _levelled(version):
    version.skills = [
        Skill(id=701, profile_version_id=version.id, name="Python", category="Expert"),
        Skill(id=702, profile_version_id=version.id, name="Go", category="Familiar"),
    ]
    return version


def test_a_skill_under_another_stored_category_is_an_error(version):
    """"Expert: Python, Go" for a Go stored as Familiar claims expert Go."""
    version = _levelled(version)
    document = a_document(skills=[TailoredSkillGroup(category="Expert", skills=["Python", "Go"])])
    assert grounding.REASON_SKILL_UNDER_WRONG_HEADING in reasons(grounding.validate(document, version))


def test_the_inflated_heading_is_repaired_by_refiling_each_skill_under_its_own(version):
    version = _levelled(version)
    document = a_document(skills=[TailoredSkillGroup(category="Expert", skills=["Python", "Go"])])
    settled = settle(document, version, repair_allowed=True)
    assert settled.report.passed
    assert {group.category: group.skills for group in settled.document.skills} == {
        "Expert": ["Python"],
        "Familiar": ["Go"],
    }


def test_skills_under_their_own_stored_category_pass(version):
    version = _levelled(version)
    document = a_document(
        skills=[
            TailoredSkillGroup(category="Expert", skills=["Python"]),
            TailoredSkillGroup(category="Familiar", skills=["Go"]),
        ]
    )
    assert grounding.REASON_SKILL_UNDER_WRONG_HEADING not in reasons(grounding.validate(document, version))


# --- the default generator, on profile shapes the API accepts ------------------------


def test_the_deterministic_document_passes_with_spacing_variants_and_a_blank_category():
    from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job

    version = confirmed_version()
    version.skills = [
        *version.skills,
        Skill(id=990, profile_version_id=version.id, name="Terraform ", category="Cloud & Tooling"),
        Skill(id=991, profile_version_id=version.id, name="Rust", category="   "),
    ]
    job, requirements = structured_job(cases()["cases"][0]["job"])
    document = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document

    report = grounding.validate(document, version)
    assert report.passed, [(f.field_path, f.reason) for f in report.errors]
