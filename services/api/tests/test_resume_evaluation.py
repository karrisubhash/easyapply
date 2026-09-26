"""The evaluation framework.

What is asserted here is the framework, never a score. A threshold checked into
CI before anyone knows what a good score looks like would freeze today's
accident into tomorrow's requirement — and the first prompt change that improved
nine cases and cost one would go red for the wrong reason.

So: that the dataset is well formed, that scoring can tell a right answer from a
wrong one, and that it counts an invention as an invention.
"""

import pytest

from app.schemas.resume_candidate import (
    CandidateDate,
    CandidateEducation,
    CandidateExperience,
    CandidateSkill,
    ResumeCandidate,
)
from evaluation.runner import COMPARED_FIELDS, as_document, load_cases, run, score_case

REQUIRED_SHAPES = {
    "conventional single-column resume",
    "two-column resume",
    "resume with tables",
    "sparse resume",
    "resume with missing dates",
    "resume with month-only dates",
    "multiple jobs, one employer",
    "resume with projects and certifications",
    "unusual formatting",
    "scanned resume (OCR output)",
}


@pytest.fixture(scope="module")
def dataset():
    return load_cases()


# --- the dataset -------------------------------------------------------------


def test_the_dataset_covers_the_shapes_a_resume_can_take(dataset):
    """Coverage of awkwardness, not volume. A hundred conventional resumes
    would score well and tell us nothing."""
    shapes = {case["shape"] for case in dataset["cases"]}
    assert REQUIRED_SHAPES <= shapes


def test_every_case_is_identifiable_and_has_text(dataset):
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids)), "case ids must be unique to be comparable"

    for case in dataset["cases"]:
        assert case["pages"], case["id"]
        assert case["expected"], case["id"]
        assert case.get("note"), f"{case['id']} should say what it is testing"


def test_expectations_only_use_fields_the_scorer_compares(dataset):
    """An expectation the scorer ignores is a claim nobody checks; one it cannot
    find marks a correct reading wrong."""
    for case in dataset["cases"]:
        for section, items in case["expected"].items():
            assert section in COMPARED_FIELDS, f"{case['id']}: {section}"
            for item in items:
                unknown = set(item) - set(COMPARED_FIELDS[section])
                assert not unknown, f"{case['id']}/{section}: {unknown}"


def test_the_dataset_is_versioned(dataset):
    """A score is only comparable to another score over the same expectations."""
    assert dataset["version"] == "1"


# --- scoring -----------------------------------------------------------------


def _case(expected):
    return {"id": "t", "shape": "test", "pages": ["text"], "expected": expected}


def test_a_perfect_reading_scores_perfectly():
    case = _case({"skills": [{"name": "Python"}, {"name": "Django"}]})
    candidate = ResumeCandidate(
        skills=[CandidateSkill(name="Python"), CandidateSkill(name="Django")]
    )

    score = score_case(case, candidate)
    assert score.recall == 1.0
    assert score.precision == 1.0
    assert score.fabrications == 0


def test_a_missed_item_costs_recall_but_not_precision():
    case = _case({"skills": [{"name": "Python"}, {"name": "Django"}]})
    candidate = ResumeCandidate(skills=[CandidateSkill(name="Python")])

    score = score_case(case, candidate)
    assert score.recall == 0.5
    assert score.precision == 1.0
    assert score.fabrications == 0


def test_an_invented_item_is_counted_as_a_fabrication():
    """The asymmetry the whole provenance design exists for: a gap is visible,
    an invention is not."""
    case = _case({"skills": [{"name": "Python"}]})
    candidate = ResumeCandidate(
        skills=[CandidateSkill(name="Python"), CandidateSkill(name="COBOL")]
    )

    score = score_case(case, candidate)
    assert score.recall == 1.0
    assert score.precision == 0.5
    assert score.fabrications == 1
    assert score.sections["skills"].fabricated == ["cobol"]


def test_an_invented_grade_is_caught():
    """A grade is the classic hallucination: plausible, expected by the shape of
    the section, and absent from the document."""
    case = _case({"education": [{"institution": "KTH", "degree": "MSc", "grade": None}]})
    candidate = ResumeCandidate(
        education=[CandidateEducation(institution="KTH", degree="MSc", grade="First")]
    )

    assert score_case(case, candidate).fabrications == 1


def test_scoring_ignores_case_and_spacing():
    """Typography is not a claim a resume makes."""
    case = _case({"skills": [{"name": "PostgreSQL"}]})
    candidate = ResumeCandidate(skills=[CandidateSkill(name="  postgresql ")])

    assert score_case(case, candidate).recall == 1.0


def test_a_date_read_too_precisely_does_not_match():
    """The rule the domain is built around: 'May 2024' read to the day is wrong
    even though the date is right."""
    case = _case(
        {
            "experiences": [
                {
                    "company": "Acme",
                    "job_title": "Engineer",
                    "start_date": {"value": "2024-05-01", "precision": "month"},
                    "end_date": None,
                }
            ]
        }
    )
    from datetime import date

    too_precise = ResumeCandidate(
        experiences=[
            CandidateExperience(
                company="Acme",
                job_title="Engineer",
                start_date=CandidateDate(
                    raw="May 2024", value=date(2024, 5, 1), precision="day"
                ),
            )
        ]
    )
    assert score_case(case, too_precise).recall == 0.0

    honest = ResumeCandidate(
        experiences=[
            CandidateExperience(
                company="Acme",
                job_title="Engineer",
                start_date=CandidateDate(
                    raw="May 2024", value=date(2024, 5, 1), precision="month"
                ),
            )
        ]
    )
    assert score_case(case, honest).recall == 1.0


def test_a_section_the_case_makes_no_claim_about_is_not_scored():
    """Silence is not an expectation of emptiness."""
    case = _case({"skills": [{"name": "Python"}]})
    candidate = ResumeCandidate(
        skills=[CandidateSkill(name="Python")],
        projects=[],
    )
    assert set(score_case(case, candidate).sections) == {"skills"}


# --- running the whole thing -------------------------------------------------


def test_the_deterministic_structurer_runs_over_every_case():
    """The framework works end to end and no case crashes the structurer.

    No threshold: the number this produces is a baseline to compare against,
    not a bar to clear.
    """
    scores = run()

    assert len(scores) == len(load_cases()["cases"])
    assert all(score.error is None for score in scores)
    assert all(0.0 <= score.recall <= 1.0 for score in scores)


def test_ocr_cases_can_be_excluded_where_no_engine_exists():
    with_ocr = run()
    without = run(include_ocr=False)
    assert len(without) < len(with_ocr)


def test_a_case_becomes_a_document_the_pipeline_would_recognise():
    case = load_cases()["cases"][0]
    document = as_document(case)

    assert document.page_count == len(case["pages"])
    assert document.pages[0].page_number == 1
    # Page markers are what makes a citation checkable.
    assert '<page number="1">' in document.text()
