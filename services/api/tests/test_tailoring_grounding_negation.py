"""A line may not deny what its quote affirms.

"not", "no", "without" and "nor" are connectives to grounding's token check —
they state no fact of their own — so a rewrite that inverted its quote ("Did
not build the Kafka pipeline", cited to "Built the Kafka pipeline") used to pass
with one reworded warning, and that warning told the user "the facts behind it
are" in their profile. A negation the quote does not carry is now an error the
repair pass removes, and the warning no longer vouches for what it could not
check.
"""

from __future__ import annotations

import pytest

from app.schemas.tailored_resume import TailoredLine
from app.services.tailoring import grounding
from app.services.tailoring.reconciliation import settle
from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job
from app.services.tailoring.generation import DeterministicTailor, TailoringInputs

QUOTE = "Mentored 4 engineers through design review and on-call onboarding."


def _document_with(text: str, quote: str = QUOTE):
    version = confirmed_version()
    job, requirements = structured_job(cases()["cases"][0]["job"])
    document = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document
    northwind = document.experience[0]
    northwind.bullets = [
        TailoredLine(text=text, source_quote=quote, source_field="description"),
        *[bullet for bullet in northwind.bullets if bullet.source_quote != quote],
    ]
    return document, version


@pytest.mark.parametrize(
    "text",
    [
        "Never mentored engineers through design review and on-call onboarding.",
        "Did not mentor 4 engineers through design review and on-call onboarding.",
        "Didn't mentor 4 engineers through design review or on-call onboarding.",
        "Mentored 4 engineers without design review and on-call onboarding.",
        "Mentored no engineers through design review and on-call onboarding.",
    ],
)
def test_a_negation_the_quote_does_not_carry_is_an_error(text):
    document, version = _document_with(text)
    report = grounding.validate(document, version)
    assert grounding.REASON_NEGATION_ADDED in {finding.reason for finding in report.errors}


def test_the_inverted_line_is_removed_by_repair_and_the_rest_delivered():
    document, version = _document_with(
        "Never mentored engineers through design review and on-call onboarding."
    )
    settled = settle(document, version, repair_allowed=True)
    assert settled.report.passed
    texts = [bullet.text for bullet in settled.document.experience[0].bullets]
    assert not any(text.startswith("Never") for text in texts)


def test_the_deterministic_documents_carry_no_added_negation():
    version = confirmed_version()
    for case in cases()["cases"]:
        job, requirements = structured_job(case["job"])
        document = DeterministicTailor().generate(
            TailoringInputs(version=version, job=job, requirements=requirements)
        ).document
        report = grounding.validate(document, version)
        assert grounding.REASON_NEGATION_ADDED not in {f.reason for f in report.findings}, case["id"]


def test_the_rewording_warning_no_longer_vouches_for_the_facts():
    assert "facts behind it are" not in grounding.REASON_REWORDED
