"""Is the tailored resume any good?

The tailoring suite already answers "is it grounded" — exhaustively, in
`test_tailoring_grounding.py`, where every case is a lie a generator might tell.
This file answers the other half, and it exists because the first half passed
perfectly on a document nobody could send: no name on it, the summary repeating
three bullets printed two inches below, twenty skills in alphabetical order, and
a claim to target a Django requirement it contained no Django for.

Every assertion here is a property of the generated document that a machine can
check, taken from `evaluation/datasets/tailoring_v1/cases.json` — authored from
the resume and the postings BEFORE the generator was changed, and not edited to
match an output. There is no human quality score here, because a judgement
nobody can re-run is not a regression test.

One profile, four postings, chosen so the candidate stands in a different
relation to each. The load-bearing one is `frontend_react`, where the candidate
matches almost nothing: an engine that looks good on a strong match and reaches
on a weak one is an engine that will eventually put a word on a resume that the
user has to defend in a room.
"""

from __future__ import annotations

import io
import re

import pytest
from docx import Document

from app.services.tailoring import grounding
from app.services.tailoring.generation import (
    DeterministicTailor,
    TailoringInputs,
    requirement_labels,
)
from app.services.tailoring.rendering import render
from evaluation.datasets.tailoring_v1.inputs import (
    cases,
    confirmed_version,
    structured_job,
)

DATASET = cases()
INVARIANTS = DATASET["invariants"]
CASES = {case["id"]: case for case in DATASET["cases"]}
CASE_IDS = sorted(CASES)


@pytest.fixture(scope="module")
def version():
    return confirmed_version()


@pytest.fixture(scope="module")
def generated(version):
    """Every case generated once. Selection is pure, so this is safe to share."""
    out = {}
    for case_id, case in CASES.items():
        job, requirements = structured_job(case["job"])
        document = DeterministicTailor().generate(
            TailoringInputs(version=version, job=job, requirements=requirements)
        ).document
        report = grounding.validate(
            document,
            version,
            job_requirement_labels=frozenset(requirement_labels(requirements)),
        )
        out[case_id] = (document, report)
    return out


def _document(generated, case_id):
    return generated[case_id][0]


def _lead_group(document):
    """The group holding the skills this posting named, or None."""
    for group in document.skills:
        if group.category == "Relevant to this role":
            return group
    return None


def _all_text(document) -> str:
    """Every word the document puts in front of a reader."""
    parts: list[str] = []
    if document.contact is not None:
        parts += [
            value
            for value in (
                document.contact.full_name,
                document.contact.email,
                document.contact.phone,
                document.contact.location,
            )
            if value
        ]
        parts += [link.label or "" for link in document.contact.links]
    if document.headline is not None:
        parts.append(document.headline.text)
    parts += [line.text for line in document.summary]
    for group in document.skills:
        parts.append(group.category)
        parts += group.skills
    for entry in document.experience:
        parts += [entry.job_title, entry.company, entry.location or ""]
        parts += [bullet.text for bullet in entry.bullets]
    for project in document.projects:
        parts += [project.name, project.role or ""]
        parts += project.technologies
        parts += [bullet.text for bullet in project.bullets]
    for entry in document.education:
        parts += [entry.institution, entry.degree or "", entry.field_of_study or ""]
    for entry in document.certifications:
        parts += [entry.name, entry.issuing_organization or ""]
    return " ".join(parts)


def _lines(document) -> list[str]:
    """Every sentence the document prints, for the duplication check."""
    out = [line.text for line in document.summary]
    for entry in document.experience:
        out += [bullet.text for bullet in entry.bullets]
    for project in document.projects:
        out += [bullet.text for bullet in project.bullets]
    return out


def _docx_text(document) -> str:
    rendered = Document(io.BytesIO(render(document)))
    return "\n".join(paragraph.text for paragraph in rendered.paragraphs)


# --- invariants: what "a resume" means, whatever job it is aimed at -----------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_document_says_who_it_is_from(generated, case_id):
    """The defect that started this. A flawlessly grounded document with no name
    on it, no address and no way for an employer to reply is not a resume."""
    contact = _document(generated, case_id).contact
    assert contact is not None
    expected = INVARIANTS["contact"]
    assert contact.full_name == expected["full_name"]
    assert contact.email == expected["email"]
    assert contact.phone == expected["phone"]
    assert contact.location == expected["location"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_document_carries_the_candidates_links(generated, case_id):
    contact = _document(generated, case_id).contact
    assert len(contact.links) >= INVARIANTS["min_links"]
    labels = [link.label for link in contact.links]
    assert len(labels) == len(set(labels)), "a header repeating one label is noise"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_section_a_resume_needs_is_present(generated, case_id):
    document = _document(generated, case_id)
    assert document.contact is not None
    assert document.experience
    assert document.education
    assert document.skills


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_career_is_shown_whole_and_in_order(generated, case_id):
    """Chronological, and nothing dropped. Reordering a career is a lie about it,
    and a missing job is a gap the reader will ask about."""
    document = _document(generated, case_id)
    assert [entry.company for entry in document.experience] == INVARIANTS[
        "experience_order"
    ]
    assert len(document.experience) == INVARIANTS["experience_entries"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_job_is_left_without_a_bullet(generated, case_id):
    """A job with nothing under it reads as one the candidate is hiding something
    about — including on a posting that job answers nothing of."""
    for entry in _document(generated, case_id).experience:
        assert entry.bullets, f"{entry.company} has no bullets"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_sentence_is_printed_twice(generated, case_id):
    """The summary used to be the three highest-scoring experience clauses, which
    were also three of the bullets printed immediately below it."""
    lines = [" ".join(line.split()).casefold() for line in _lines(_document(generated, case_id))]
    duplicates = {line for line in lines if lines.count(line) > 1}
    assert not duplicates, f"printed twice: {duplicates}"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_document_is_grounded(generated, case_id):
    """Usefulness never comes out of the grounding budget."""
    report = generated[case_id][1]
    assert len(report.errors) == INVARIANTS["grounding_errors"], [
        finding.reason for finding in report.errors
    ]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_number_is_invented(generated, case_id):
    """Every digit-bearing token in the document appears in the profile.

    Grounding checks this per line against the line's own source. This checks it
    once more over the whole document against the whole profile, because a
    number is the claim an interviewer is most likely to test."""
    version = confirmed_version()
    known: set[str] = set()
    for text in (
        [version.summary or ""]
        + [row.description or "" for row in version.experiences]
        + [row.job_title for row in version.experiences]
        + [row.description or "" for row in version.projects]
        + [row.name for row in version.certifications]
        + [str(row.start_date) for row in version.experiences]
    ):
        known |= set(re.findall(r"[A-Za-z0-9][A-Za-z0-9+.%#/_-]*", text.casefold()))

    for line in _lines(_document(generated, case_id)):
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+.%#/_-]*", line.casefold()):
            if any(character.isdigit() for character in token):
                assert token in known, f"{token!r} is not in the profile"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_docx_renders_and_carries_the_name(generated, case_id):
    text = _docx_text(_document(generated, case_id))
    assert INVARIANTS["contact"]["full_name"] in text
    assert INVARIANTS["contact"]["email"] in text


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_docx_carries_none_of_our_exhaust(generated, case_id):
    """This file is sent to an employer under the candidate's name. A line naming
    the tool that produced it, or telling that employer a machine aimed this at
    their vacancy, is our plumbing printed on the user's resume."""
    text = _docx_text(_document(generated, case_id))
    for phrase in INVARIANTS["docx_excludes"]:
        assert phrase not in text, f"{phrase!r} is on the rendered resume"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_docx_does_not_invent_date_precision(generated, case_id):
    """The resume says "2015 - 2018" and "2023". Printing "Jan 2015" states a
    month the source never gave — a false fact introduced after grounding has
    finished looking, which is the worst place to put one."""
    text = _docx_text(_document(generated, case_id))
    assert INVARIANTS["date_precision_preserved"]["education"] in text
    assert f"({INVARIANTS['date_precision_preserved']['certifications']})" in text
    assert "Jan 2015" not in text
    assert "Jan 2023" not in text


# --- per case: did selection actually move? -----------------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_posting_s_own_skills_lead_the_skills_section(generated, case_id):
    expect = CASES[case_id]["expect"]
    group = _lead_group(_document(generated, case_id))
    assert group is not None
    for name in expect["lead_skills_include"]:
        assert name in group.skills, f"{name} is named by the posting and not led with"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_irrelevant_skills_do_not_lead(generated, case_id):
    expect = CASES[case_id]["expect"]
    if "lead_skills_exclude" not in expect:
        pytest.skip("this case states no exclusions")
    group = _lead_group(_document(generated, case_id))
    for name in expect["lead_skills_exclude"]:
        assert name not in group.skills


def test_the_lead_skills_are_in_the_postings_order(generated):
    """A skills line is scanned, not read. The skill the advert led with belongs
    where a scan starts — not wherever the alphabet put it."""
    expect = CASES["backend_senior"]["expect"]
    group = _lead_group(_document(generated, "backend_senior"))
    positions = [group.skills.index(name) for name in expect["lead_skills_order_follows_posting"]]
    assert positions == sorted(positions), group.skills


def test_a_tailored_resume_drops_some_of_what_is_true(generated):
    """The one place selection drops a true thing, and the point of the feature.
    Twenty comma-separated words say less than eight."""
    version = confirmed_version()
    document = _document(generated, "backend_senior")
    shown = {name for group in document.skills for name in group.skills}
    assert len(shown) < len(version.skills)
    # ...but never one the posting asked for.
    group = _lead_group(document)
    for name in CASES["backend_senior"]["expect"]["lead_skills_include"]:
        assert name in group.skills


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_right_bullet_leads(generated, case_id):
    expect = CASES[case_id]["expect"]
    if "leading_bullet" not in expect:
        pytest.skip("this case states no leading bullet")
    document = _document(generated, case_id)
    for company, needle in expect["leading_bullet"].items():
        if company == "why":
            continue
        entry = next(e for e in document.experience if e.company == company)
        assert needle.casefold() in entry.bullets[0].text.casefold()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_evidence_the_posting_asks_for_is_not_dropped(generated, case_id):
    """The bullets that token-overlap scored at zero: the only on-call evidence
    in the profile, and the only healthcare evidence."""
    expect = CASES[case_id]["expect"]
    if "bullet_selected" not in expect:
        pytest.skip("this case names no bullet")
    document = _document(generated, case_id)
    for company, needle in expect["bullet_selected"].items():
        if company == "why":
            continue
        entry = next(e for e in document.experience if e.company == company)
        assert any(
            needle.casefold() in bullet.text.casefold() for bullet in entry.bullets
        ), f"{needle!r} is the evidence this posting asks for and {company} dropped it"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_relevant_projects_are_the_ones_carried(generated, case_id):
    expect = CASES[case_id]["expect"]
    names = [project.name for project in _document(generated, case_id).projects]
    for name in expect.get("projects_include", []):
        assert name in names
    for name in expect.get("projects_exclude", []):
        assert name not in names
    if "projects_first" in expect:
        assert names[0] == expect["projects_first"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_certifications_the_posting_wants_are_shown(generated, case_id):
    expect = CASES[case_id]["expect"]
    if "certifications_include" not in expect:
        pytest.skip("this case names no certification")
    names = {entry.name for entry in _document(generated, case_id).certifications}
    for name in expect["certifications_include"]:
        assert name in names


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_metrics_survive_tailoring(generated, case_id):
    """Selection must not quietly drop the achievements carrying the numbers."""
    expect = CASES[case_id]["expect"]
    if "metrics_preserved" not in expect:
        pytest.skip("this case names no metric")
    text = _all_text(_document(generated, case_id))
    for metric in expect["metrics_preserved"]:
        assert metric in text, f"{metric} was selected away"


# --- targeting: what the document says it aimed at -----------------------------------


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_document_claims_the_requirements_it_answers(generated, case_id):
    expect = CASES[case_id]["expect"]
    claimed = _document(generated, case_id).targeted_requirements
    for label in expect.get("targeted_include", []):
        assert label in claimed, f"{label!r} is answered and not claimed"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_document_claims_nothing_it_does_not_answer(generated, case_id):
    """The field a reviewer reads to decide whether the document aimed correctly.
    A generous rule here does not flatter the document, it misleads them."""
    expect = CASES[case_id]["expect"]
    claimed = _document(generated, case_id).targeted_requirements
    for label in expect.get("targeted_exclude", []):
        assert label not in claimed, f"{label!r} is claimed and not answered"


# --- the weak match: the case that matters -------------------------------------------


def test_nothing_is_reached_for_on_a_posting_the_candidate_does_not_match():
    """A backend engineer against React, JavaScript and Jest.

    None of those is anywhere in the profile. This is where a tailoring engine
    under pressure to look relevant puts a word the user cannot defend, so the
    assertion is the blunt one: those strings do not appear in the document at
    all, in any section, at any position.
    """
    version = confirmed_version()
    job, requirements = structured_job(CASES["frontend_react"]["job"])
    document = DeterministicTailor().generate(
        TailoringInputs(version=version, job=job, requirements=requirements)
    ).document

    haystack = (_all_text(document) + " " + _docx_text(document)).casefold()
    for forbidden in CASES["frontend_react"]["expect"]["never_appears"]:
        assert forbidden.casefold() not in haystack, f"{forbidden!r} reached onto the resume"


def test_a_weak_match_is_still_a_resume(generated):
    """Emitting nothing is not honesty, it is a broken document."""
    document = _document(generated, "frontend_react")
    assert document.experience
    assert document.education
    assert document.contact is not None
    assert any(entry.bullets for entry in document.experience)


def test_two_postings_do_not_produce_the_same_document(generated):
    """The feature, stated as a test. One profile, two postings, and if the
    output does not move then nothing was tailored."""
    backend = _document(generated, "backend_senior")
    platform = _document(generated, "platform_k8s")

    assert _lead_group(backend).skills != _lead_group(platform).skills
    assert [p.name for p in backend.projects] != [p.name for p in platform.projects]
    assert backend.targeted_requirements != platform.targeted_requirements


# --- the free-text pairing: one token, two opposite right answers --------------------


def test_the_same_token_does_not_carry_two_opposite_verdicts(generated):
    """The hardest case in the dataset, asserted as one test so it cannot be
    half-satisfied.

    The candidate's summary says "...across two regulated industries". That
    single word `regulated` is the only support in the entire document for BOTH
    of these, one from each of two postings:

        j_platform_k8s  "Experience in regulated financial services"
                        -> WRONG. They have worked in logistics and healthcare.

        j_sre_health    "Experience in healthcare or another regulated domain"
                        -> RIGHT. The posting accepts any other regulated
                           domain, and theirs is one.

    Every free-text claim in this dataset rested on exactly one matched token, so
    no threshold on how MANY matched can separate these — and proportion ranks
    them the wrong way round, since the wrong one matches one word of two and the
    right one matches one word of four.

    Asserting them together is the point. Satisfying either alone is easy: refuse
    all partial evidence and the first passes while the second breaks; accept it
    and the reverse. Only reading what each requirement will ACCEPT gets both.
    """
    platform = _document(generated, "platform_k8s").targeted_requirements
    sre = _document(generated, "sre_health").targeted_requirements

    assert "Experience in regulated financial services" not in platform
    assert "Experience in healthcare or another regulated domain" in sre


def test_a_term_of_art_still_carries_the_on_call_requirement(generated):
    """The other single-token claim that is correct. "Mentored 4 engineers
    through design review and on-call onboarding" is the only on-call evidence in
    the profile, and `on-call` is a named practice rather than an English word —
    which is what lets it stand alone where `regulated` may not."""
    assert "Participation in an on-call rotation" in _document(
        generated, "sre_health"
    ).targeted_requirements


def test_tightening_free_text_left_the_documents_alone(generated):
    """Selection is not targeting. The rule that decides what a document may
    CLAIM was tightened; the rule that decides what it SAYS was not, and these
    are the assertions that would notice if the widened stopword list had
    quietly changed which bullets were chosen.
    """
    backend = _document(generated, "backend_senior")
    platform = _document(generated, "platform_k8s")

    assert _lead_group(backend).skills[:4] == ["Python", "Django", "PostgreSQL", "Docker"]
    assert [project.name for project in backend.projects] == [
        "Ledger Reconciliation Engine"
    ]
    assert [project.name for project in platform.projects][0] == "Observability Starter Kit"
    assert "Python" in backend.experience[0].bullets[0].text
