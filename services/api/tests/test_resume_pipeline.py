"""The processing pipeline, against a real database.

Nothing here mocks the database. Storage and the AI structurer are substituted,
because those are the two genuine external boundaries.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    ResumeDocumentPage,
    ResumeProcessingEvent,
    ResumeProcessingRun,
    ResumeStructuredResult,
)
from app.models.profile_version import CREATED_BY_AI, STATUS_CONFIRMED, STATUS_DRAFT
from app.models.resume_processing import (
    ALLOWED_TRANSITIONS,
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
)
from app.schemas.resume_candidate import (
    CandidateCertification,
    CandidateDate,
    CandidateEducation,
    CandidateExperience,
    CandidateSkill,
    Provenance,
    ResumeCandidate,
)
from app.services import profile_service
from app.services.errors import ConflictError
from app.services.resume import pipeline, service
from app.services.resume.structuring import StructuringError, StructuringResult
from app.services.storage import InMemoryStorage
from tests.resume_fixtures import (
    make_docx,
    make_image_only_pdf,
    make_resume_docx,
    make_resume_pdf,
)


@pytest.fixture
def storage():
    return InMemoryStorage()


class FakeStructurer:
    """Stands in for a structurer, returning whatever a test needs it to read.

    Returns a StructuringResult like the real ones do, so the attribution the
    pipeline persists is exercised rather than stubbed past.
    """

    name = "fake"
    model = "fake-model"

    def __init__(self, candidate=None, error=None, *, provider="fake-provider"):
        self._candidate = candidate if candidate is not None else ResumeCandidate()
        self._error = error
        self._provider = provider

    def structure(self, document):
        if self._error:
            raise self._error
        return StructuringResult(
            candidate=self._candidate,
            structurer=self.name,
            provider=self._provider,
            model=self.model,
            prompt_version="test",
            metadata={"latency_ms": 1},
        )


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, text="RECOVERED", available=True):
        self._text = text
        self._available = available

    def is_available(self):
        return self._available

    def image_to_text(self, image):
        return self._text


def upload(db, user, storage, data=None, filename="resume.pdf", media="application/pdf"):
    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename=filename,
        declared_media_type=media,
        data=data if data is not None else make_resume_pdf(),
        storage=storage,
    )
    return document


def run_pipeline(db, document, storage, **kwargs):
    run = service.start_run(db, document=document)
    return pipeline.process_run(db, run_id=run.id, storage=storage, **kwargs)


# --- the happy path ----------------------------------------------------------


def test_a_pdf_resume_becomes_a_draft_profile_version(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage)

    assert run.status == "completed"
    assert run.failed_stage is None
    assert run.page_count == 2
    assert run.extracted_char_count > 0
    assert run.ocr_page_count == 0
    assert run.profile_version_id is not None

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    assert version.status == STATUS_DRAFT
    assert version.created_by_type == CREATED_BY_AI
    assert {skill.name for skill in version.skills} >= {"Python", "Django", "PostgreSQL"}


def test_a_docx_resume_is_processed_too(db, user, storage):
    document = upload(
        db,
        user,
        storage,
        data=make_resume_docx(),
        filename="resume.docx",
        media="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    run = run_pipeline(db, document, storage)

    assert run.status == "completed"
    assert run.page_count == 1


def test_extracted_pages_are_stored_with_their_method(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage)

    pages = db.query(ResumeDocumentPage).filter_by(resume_processing_run_id=run.id).all()
    assert [page.page_number for page in pages] == [1, 2]
    assert all(page.extraction_method == "text" for page in pages)
    assert all(page.char_count > 0 for page in pages)


def test_every_stage_is_timed(db, user, storage):
    """Requirement: which stage failed, and how long did each take."""
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage)

    events = db.query(ResumeProcessingEvent).filter_by(resume_processing_run_id=run.id).all()
    stages = {event.stage for event in events}
    assert {"validate", "extract", "structure", "validate_result", "materialise"} <= stages
    assert all(
        event.duration_ms is not None for event in events if event.outcome == "succeeded"
    )


def test_the_structured_result_records_who_produced_it(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=FakeStructurer())

    stored = db.query(ResumeStructuredResult).filter_by(resume_processing_run_id=run.id).one()
    assert stored.structurer == "fake"
    assert stored.model == "fake-model"


# --- OCR ---------------------------------------------------------------------


def test_a_scanned_pdf_is_routed_through_ocr(db, user, storage):
    document = upload(db, user, storage, data=make_image_only_pdf(1))
    run = run_pipeline(
        db,
        document,
        storage,
        ocr_engine=FakeOcr(text="EXPERIENCE\nEngineer at Somewhere"),
        structurer=FakeStructurer(),
    )

    assert run.status == "completed"
    assert run.ocr_page_count == 1

    page = db.query(ResumeDocumentPage).filter_by(resume_processing_run_id=run.id).one()
    assert page.extraction_method == "ocr"


def test_a_scan_fails_cleanly_when_ocr_is_unavailable(db, user, storage):
    """An empty result would become a confidently empty resume the user confirms."""
    document = upload(db, user, storage, data=make_image_only_pdf(1))
    run = run_pipeline(db, document, storage, ocr_engine=FakeOcr(available=False))

    assert run.status == "failed"
    assert run.failed_stage == "ocr"
    assert run.error_code == "ocr_unavailable"
    assert run.profile_version_id is None


def test_ocr_that_recovers_nothing_is_a_failure(db, user, storage):
    document = upload(db, user, storage, data=make_image_only_pdf(1))
    run = run_pipeline(db, document, storage, ocr_engine=FakeOcr(text="   "))

    assert run.status == "failed"
    assert run.error_code == "no_text_extracted"


# --- failures ----------------------------------------------------------------


def test_a_missing_stored_object_fails_at_validation(db, user, storage):
    document = upload(db, user, storage)
    storage.delete(document.storage_key)

    run = run_pipeline(db, document, storage)
    assert run.status == "failed"
    assert run.failed_stage == "validate"
    assert run.error_code == "stored_object_missing"


def test_a_structurer_failure_is_reported_against_its_stage(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(
        db,
        document,
        storage,
        structurer=FakeStructurer(error=StructuringError("ai_unreachable", "nope")),
    )

    assert run.status == "failed"
    assert run.failed_stage == "structure"
    assert run.error_code == "ai_unreachable"


def test_an_unexpected_structurer_crash_does_not_leak_its_message(db, user, storage):
    """Parser and model exceptions quote document content; that is the user's PII."""
    document = upload(db, user, storage)
    secret = "CANDIDATE HOME ADDRESS 42 Privacy Lane"
    run = run_pipeline(
        db, document, storage, structurer=FakeStructurer(error=RuntimeError(secret))
    )

    assert run.status == "failed"
    assert run.error_code == "structuring_failed"
    assert secret not in (run.error_message or "")


def test_a_failed_run_creates_no_profile_version(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(
        db, document, storage, structurer=FakeStructurer(error=StructuringError("x", "y"))
    )

    assert run.status == "failed"
    with pytest.raises(profile_service.NotFoundError):
        profile_service.get_version(db, user_id=user.id, version_number=1)


def test_a_failed_stage_is_recorded_in_the_event_log(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(
        db, document, storage, structurer=FakeStructurer(error=StructuringError("boom", "y"))
    )

    failures = [
        event
        for event in db.query(ResumeProcessingEvent).filter_by(resume_processing_run_id=run.id)
        if event.outcome == "failed"
    ]
    assert [event.stage for event in failures] == ["structure"]
    assert failures[0].detail["code"] == "boom"


# --- state machine -----------------------------------------------------------


def test_the_transition_table_is_acyclic_from_terminal_states():
    for terminal in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_only_failed_is_retryable():
    assert RETRYABLE_STATUSES == frozenset({"failed"})


def test_the_database_refuses_an_illegal_transition(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    run.status = "structuring"  # skipping validating and extracting
    with pytest.raises(IntegrityError, match="illegal processing transition"):
        db.flush()
    db.rollback()


def test_the_database_refuses_a_run_created_in_a_later_state(db, user, storage):
    document = upload(db, user, storage)
    db.add(ResumeProcessingRun(resume_document_id=document.id, attempt=1, status="completed"))

    with pytest.raises(IntegrityError, match="must start in uploaded"):
        db.flush()
    db.rollback()


def test_a_completed_run_cannot_be_reopened(db, user, storage):
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage)

    run.status = "extracting"
    with pytest.raises(IntegrityError, match="illegal processing transition"):
        db.flush()
    db.rollback()


def test_the_python_guard_refuses_before_the_database_does(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    with pytest.raises(pipeline.IllegalTransition):
        pipeline.advance(run, "completed")


def test_an_uploaded_document_is_immutable(db, user, storage):
    document = upload(db, user, storage)
    document.original_filename = "something-else.pdf"

    with pytest.raises(IntegrityError, match="immutable"):
        db.flush()
    db.rollback()


# --- retries -----------------------------------------------------------------


def test_a_retry_is_a_new_run_not_a_reopened_one(db, user, storage):
    document = upload(db, user, storage)
    first = run_pipeline(
        db, document, storage, structurer=FakeStructurer(error=StructuringError("x", "y"))
    )
    second = run_pipeline(db, document, storage)

    assert first.id != second.id
    assert (first.attempt, second.attempt) == (1, 2)
    assert first.status == "failed"
    assert second.status == "completed"


def test_the_failed_attempt_survives_the_successful_retry(db, user, storage):
    document = upload(db, user, storage)
    run_pipeline(db, document, storage, structurer=FakeStructurer(error=StructuringError("x", "y")))
    run_pipeline(db, document, storage)

    runs = db.query(ResumeProcessingRun).filter_by(resume_document_id=document.id).all()
    assert len(runs) == 2
    assert {run.status for run in runs} == {"failed", "completed"}


# --- mapping and provenance --------------------------------------------------


def test_an_item_the_snapshot_cannot_hold_is_reported_not_dropped(db, user, storage):
    """An experience with no date cannot be stored, and must not be invented."""
    candidate = ResumeCandidate(
        experiences=[
            CandidateExperience(
                company="Acme",
                job_title="Engineer",
                start_date=CandidateDate(raw="sometime", value=None, precision=None),
                provenance=[Provenance(page=1, quote="Acme", confidence=0.4)],
            )
        ]
    )
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    stored = db.query(ResumeStructuredResult).filter_by(resume_processing_run_id=run.id).one()
    assert stored.unmapped
    assert stored.unmapped[0]["section"] == "experiences"
    assert "start date" in stored.unmapped[0]["reason"]

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    assert version.experiences == []


def test_date_precision_survives_into_the_draft(db, user, storage):
    from datetime import date

    candidate = ResumeCandidate(
        experiences=[
            CandidateExperience(
                company="APTRANSCO",
                job_title="Intern",
                start_date=CandidateDate(raw="May 2024", value=date(2024, 5, 1), precision="month"),
                provenance=[Provenance(page=1, quote="May 2024", confidence=0.9)],
            )
        ]
    )
    document = upload(db, user, storage)
    run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    experience = version.experiences[0]
    assert experience.start_date == date(2024, 5, 1)
    assert experience.start_date_precision == "month"


def test_a_certification_date_survives_without_a_precision(db, user, storage):
    """`certifications` has no precision column and no paired CHECK.

    So the rule that a date and its precision travel together — which exists for
    experiences, education and projects, where the database enforces it — does
    not apply here, and applying it anyway silently dropped the date. An expiry
    that goes missing is not a neutral loss: `rules.judge_certification` reads it
    to tell a lapsed credential from a live one, so the expired certification
    below would otherwise come back reading as current.
    """
    from datetime import date

    candidate = ResumeCandidate(
        certifications=[
            CandidateCertification(
                name="AWS Certified Developer",
                issue_date=CandidateDate(raw="2019", value=date(2019, 1, 1), precision=None),
                expiration_date=CandidateDate(raw="2022", value=date(2022, 1, 1), precision=None),
                provenance=[Provenance(page=1, quote="AWS Certified Developer", confidence=0.9)],
            )
        ]
    )
    document = upload(db, user, storage)
    run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    certification = version.certifications[0]
    assert certification.issue_date == date(2019, 1, 1)
    assert certification.expiration_date == date(2022, 1, 1)


def test_a_grade_is_never_invented(db, user, storage):
    candidate = ResumeCandidate(
        education=[
            CandidateEducation(
                institution="ANITS",
                degree="B.Tech",
                provenance=[Provenance(page=1, quote="ANITS", confidence=0.8)],
            )
        ]
    )
    document = upload(db, user, storage)
    run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    assert version.education[0].grade is None


def test_duplicate_skill_names_do_not_break_the_draft(db, user, storage):
    """The snapshot has a case-insensitive unique index; a resume does not."""
    candidate = ResumeCandidate(
        skills=[
            CandidateSkill(name="Python", provenance=[Provenance(page=1, quote="Python", confidence=1.0)]),
            CandidateSkill(name="python", provenance=[Provenance(page=1, quote="python", confidence=1.0)]),
            CandidateSkill(name="PYTHON", provenance=[Provenance(page=1, quote="PYTHON", confidence=1.0)]),
        ]
    )
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    assert run.status == "completed"
    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    assert [skill.name for skill in version.skills] == ["Python"]


# --- profile integration -----------------------------------------------------


def test_the_draft_stays_editable_and_confirmable(db, user, storage):
    document = upload(db, user, storage)
    run_pipeline(db, document, storage)

    edited = profile_service.replace_snapshot(
        db,
        user_id=user.id,
        version_number=1,
        snapshot=profile_service.Snapshot.model_validate({"skills": [{"name": "Rust"}]}),
    )
    assert [skill.name for skill in edited.skills] == ["Rust"]

    confirmed = profile_service.confirm_version(db, user_id=user.id, version_number=1)
    assert confirmed.status == STATUS_CONFIRMED
    assert confirmed.is_current is True


def test_a_confirmed_ai_draft_becomes_immutable(db, user, storage):
    document = upload(db, user, storage)
    run_pipeline(db, document, storage)
    profile_service.confirm_version(db, user_id=user.id, version_number=1)

    with pytest.raises(profile_service.ConflictError):
        profile_service.replace_snapshot(
            db,
            user_id=user.id,
            version_number=1,
            snapshot=profile_service.Snapshot.model_validate({}),
        )


def test_a_second_resume_produces_a_second_draft(db, user, storage):
    from tests.resume_fixtures import make_pdf

    first = upload(db, user, storage)
    run_pipeline(db, first, storage)

    # Long enough to clear the OCR threshold: a page with almost no text is
    # legitimately treated as a scan, which a two-word fixture would trip.
    second_resume = make_pdf(
        [
            "JANE SECOND\njane@example.com\n\n"
            "SKILLS\nRust, Go, Kubernetes, Terraform\n\n"
            "EXPERIENCE\nPlatform Engineer, Someplace\nJanuary 2023 - March 2025\n"
            "Ran the platform team and owned the deployment pipeline."
        ]
    )
    second = upload(db, user, storage, data=second_resume)
    run = run_pipeline(db, second, storage)
    assert run.status == "completed", run.error_code

    versions = profile_service.list_versions(db, user_id=user.id)
    assert [version.version_number for version in versions] == [1, 2]


def test_a_resume_can_be_uploaded_before_a_profile_exists(db, user, storage):
    """Upload is identity-scoped; the profile is created when it is needed."""
    with pytest.raises(profile_service.NotFoundError):
        profile_service.get_profile(db, user_id=user.id)

    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage)

    assert run.status == "completed"
    assert profile_service.get_profile(db, user_id=user.id) is not None


# --- failures that must not strand a run -------------------------------------
#
# A run left in a non-terminal state is worse than a failed one:
# require_retryable() refuses to start another while one is in flight, so a
# stranded run makes the document permanently unprocessable. Every failure must
# therefore reach a terminal state, whatever raised it.


def test_a_textless_docx_fails_at_extract_rather_than_in_a_pdf_renderer(db, user, storage):
    """OCR rasterises PDF pages, and a DOCX has none to rasterise."""
    document = upload(
        db,
        user,
        storage,
        data=make_docx(headings_and_paragraphs=[]),
        filename="empty.docx",
        media="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    # An OCR engine that reports itself available is the interesting case: the
    # document used to be handed to the PDF renderer, which cannot read it.
    run = run_pipeline(db, document, storage, ocr_engine=FakeOcr())

    assert run.status == "failed"
    assert run.failed_stage == "extract"
    assert run.error_code == "no_text_extracted"
    # Terminal and classified. An unreadable document does not become readable
    # on a second attempt, so the retry is refused with a reason rather than
    # queued to fail identically — the run is not stuck, it is answered.
    with pytest.raises(ConflictError, match="will not change"):
        service.require_retryable(service.latest_run(db, document=document))


def test_an_adapter_raising_something_unforeseen_still_ends_the_run(db, user, storage):
    class Exploding:
        name = "exploding"
        model = None

        def structure(self, document):
            raise RuntimeError("resume text that must not be logged")

    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=Exploding())

    assert run.status == "failed"
    assert run.failed_stage == "structure"
    assert ("structure", "failed") in [(e.stage, e.outcome) for e in run.events]
    # The message is ours; a library's is the user's document quoted back.
    assert "must not be logged" not in (run.error_message or "")


def test_a_failed_draft_does_not_erase_the_record_of_the_run(db, user, storage, monkeypatch):
    """The profile service owns its own transaction, and a rollback there used to
    discard this run's pages, events and counts along with the draft."""
    def refuse(*args, **kwargs):
        raise RuntimeError("the draft was refused")

    document = upload(db, user, storage)
    monkeypatch.setattr(profile_service, "create_draft_version_with_snapshot", refuse)
    run = run_pipeline(db, document, storage)

    assert run.status == "failed"
    assert run.failed_stage == "materialise"

    recorded = db.get(ResumeProcessingRun, run.id)
    assert recorded.page_count == 2
    assert recorded.extracted_char_count > 0
    assert db.query(ResumeDocumentPage).filter_by(resume_processing_run_id=run.id).count() == 2
    assert "extract" in [event.stage for event in recorded.events]


# --- a model reading a resume badly must not cost the whole snapshot ---------


def test_an_impossible_date_range_is_reported_not_rejected_wholesale(db, user, storage):
    from datetime import date

    candidate = ResumeCandidate(
        experiences=[
            CandidateExperience(
                company="Backwards Ltd",
                job_title="Engineer",
                start_date=CandidateDate(raw="2020", value=date(2020, 1, 1), precision="year"),
                end_date=CandidateDate(raw="2018", value=date(2018, 1, 1), precision="year"),
                provenance=[Provenance(page=1, quote="Backwards", confidence=0.5)],
            )
        ],
        skills=[CandidateSkill(name="Python", provenance=[])],
    )
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    # The run completes; only the impossible item is left out, and it is named.
    assert run.status == "completed", run.error_code
    stored = db.query(ResumeStructuredResult).filter_by(resume_processing_run_id=run.id).one()
    assert stored.unmapped[0]["section"] == "experiences"

    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    assert version.experiences == []
    assert [skill.name for skill in version.skills] == ["Python"]


def test_an_over_precise_number_of_years_is_rounded_to_what_the_column_holds(db, user, storage):
    from decimal import Decimal

    candidate = ResumeCandidate(
        skills=[
            CandidateSkill(name="Python", years_of_experience=3.75),
            CandidateSkill(name="Rust", years_of_experience=99999.0),
        ]
    )
    document = upload(db, user, storage)
    run = run_pipeline(db, document, storage, structurer=FakeStructurer(candidate))

    assert run.status == "completed", run.error_code
    version = profile_service.get_version(db, user_id=user.id, version_number=1)
    years = {skill.name: skill.years_of_experience for skill in version.skills}
    assert years["Python"] == Decimal("3.8")
    # A magnitude the column cannot represent is dropped, never truncated into
    # a different number.
    assert years["Rust"] is None
