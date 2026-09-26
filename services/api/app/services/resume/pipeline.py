"""Driving one processing run through the state machine.

The entry point is `process_run(db, *, run_id=...)`. That signature is the async
seam: it takes an integer and re-reads everything else, so the day a worker is
introduced, the task body is this call and nothing about the tables or the
service API changes. Nothing is passed in memory from the request that started
it, and no lock is held across the model call.

Every stage transition is written through `advance()`, which refuses an illegal
move before the database does, and every stage is timed into
`resume_processing_events` so "which stage failed and how long did each take"
is a query rather than a log search.
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    ResumeDocument,
    ResumeDocumentPage,
    ResumeProcessingEvent,
    ResumeProcessingRun,
    ResumeStructuredResult,
)
from app.models.resume_processing import (
    ALLOWED_TRANSITIONS,
    STATUS_COMPLETED,
    STATUS_EXTRACTING,
    STATUS_FAILED,
    STATUS_MATERIALISING,
    STATUS_OCR_PROCESSING,
    STATUS_OCR_REQUIRED,
    STATUS_STRUCTURING,
    STATUS_VALIDATING,
    STATUS_VALIDATING_RESULT,
)
from app.services import profile_service
from app.services.resume import mapping
from app.services.resume.failures import classification, is_retryable
from app.schemas.resume_candidate import UnmappedItem
from app.services.resume.extraction import (
    METHOD_OCR,
    UNREAD_OCR_UNAVAILABLE,
    UNREAD_PAGE_LIMIT,
    UNREAD_TOO_LARGE_TO_RENDER,
    ExtractionError,
    extract,
    mark_unread,
    must_fail_without_ocr,
    pages_needing_ocr,
    unread_pages,
)
from app.services.resume.ocr import OcrError, apply_ocr, get_ocr_engine
from app.services.resume.providers import ProviderError
from app.services.resume.structuring import (
    StructuringError,
    get_structurer,
    unplaced_links,
    verify_provenance,
)
from app.services.resume.validation import (
    PDF_MEDIA_TYPE,
    FileValidationError,
    validate_upload,
)
from app.services.storage import ObjectNotFound, StorageBackend, get_storage

logger = logging.getLogger(__name__)

#: Which stage a run was in when its status says so. Needed by anything that
#: has to end a run it did not start — the abort path here, and the reaper —
#: because failed_stage has a CHECK and "unknown" is not one of its values.
STAGE_FOR_STATUS = {
    STATUS_VALIDATING: "validate",
    STATUS_EXTRACTING: "extract",
    STATUS_OCR_REQUIRED: "ocr",
    STATUS_OCR_PROCESSING: "ocr",
    STATUS_STRUCTURING: "structure",
    STATUS_VALIDATING_RESULT: "validate_result",
    STATUS_MATERIALISING: "materialise",
}

#: Error text is written by us, from a fixed vocabulary — never str(exception).
#: Parser libraries quote document content in their messages, and that content
#: is the user's personal data; it must not end up in an error column that gets
#: read, logged and shipped to an APM.
_MAX_ERROR_MESSAGE = 400


class ProcessingFailed(Exception):
    """A stage failed. Carries where and why, in our own words."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message[:_MAX_ERROR_MESSAGE]


class IllegalTransition(RuntimeError):
    pass


def advance(run: ResumeProcessingRun, status: str) -> None:
    """Move a run to its next state, refusing anything the graph forbids."""
    if status not in ALLOWED_TRANSITIONS.get(run.status, frozenset()):
        raise IllegalTransition(f"{run.status} -> {status} is not a legal transition")
    run.status = status


def _record(
    db: Session,
    run: ResumeProcessingRun,
    *,
    stage: str,
    outcome: str,
    duration_ms: int | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        ResumeProcessingEvent(
            resume_processing_run_id=run.id,
            stage=stage,
            outcome=outcome,
            duration_ms=duration_ms,
            detail=detail,
        )
    )
    logger.info(
        "resume.stage",
        extra={
            "resume_run_id": run.id,
            "resume_document_id": run.resume_document_id,
            "stage": stage,
            "outcome": outcome,
            "duration_ms": duration_ms,
        },
    )


@contextmanager
def _stage(db: Session, run: ResumeProcessingRun, stage: str):
    """Time a stage and record how it ended, whatever happens."""
    started = time.perf_counter()
    _record(db, run, stage=stage, outcome="started")
    try:
        yield
    except ProcessingFailed as failure:
        _record(
            db,
            run,
            stage=stage,
            outcome="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"code": failure.code, "classification": classification(failure.code)},
        )
        raise
    except Exception as exc:
        # An exception we did not anticipate is still this stage's failure.
        # Letting it escape leaves the run in a non-terminal state, and
        # require_retryable() then refuses every retry — so an unforeseen error
        # would make the document permanently unprocessable rather than merely
        # failed. The message is ours, never str(exc): library exceptions quote
        # document content, which is the user's personal data.
        logger.exception("resume stage %s raised", stage)
        _record(
            db,
            run,
            stage=stage,
            outcome="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={
                "code": "unexpected_error",
                "classification": classification("unexpected_error"),
            },
        )
        raise ProcessingFailed(
            stage, "unexpected_error", "Processing stopped unexpectedly."
        ) from exc
    else:
        _record(
            db,
            run,
            stage=stage,
            outcome="succeeded",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def process_run(
    db: Session,
    *,
    run_id: int,
    storage: StorageBackend | None = None,
    structurer=None,
    ocr_engine=None,
) -> ResumeProcessingRun:
    """Run the pipeline for one run id.

    Takes an id and nothing else from the caller's memory, so this body is
    exactly what a background task would call. The optional adapters exist so
    tests can substitute a fake OCR engine or structurer without patching
    imports; production passes none of them.
    """
    run = db.get(ResumeProcessingRun, run_id)
    if run is None:
        raise LookupError(f"processing run {run_id} does not exist")

    document = db.get(ResumeDocument, run.resume_document_id)
    if document is None:
        raise LookupError(f"resume document {run.resume_document_id} does not exist")

    storage = storage or get_storage()
    structurer = structurer or get_structurer()
    ocr_engine = ocr_engine or get_ocr_engine()

    run.started_at = datetime.now(timezone.utc)
    db.flush()

    try:
        extracted = _run_validate_and_extract(db, run, document, storage, ocr_engine)
        reading = _run_structuring(db, run, extracted, structurer)
        snapshot, unmapped = _run_result_validation(db, run, reading, extracted)
        version_id = _run_materialise(db, run, document, snapshot, unmapped, reading)
    except ProcessingFailed as failure:
        try:
            fail_run(db, run, failure)
            db.commit()
        except Exception:
            # The failure could not be written — a poisoned session is the
            # likely reason, and the one thing that must not happen is the run
            # being left mid-flight. _abort() rolls back first, which is what
            # makes the session usable again.
            logger.exception("recording the failure of run %s failed", run_id)
            _abort(db, run_id)
        return run
    except Exception:
        # Outside a stage — an illegal transition, a flush between stages. Same
        # reasoning as the guard in _stage(): a run left mid-flight can never be
        # retried, so it is recorded as failed before the error goes on to
        # become the 500 it deserves to be.
        logger.exception("resume run %s stopped outside a stage", run_id)
        _abort(db, run_id)
        raise

    # profile_version_id and the completed status must land in one statement:
    # ck_resume_processing_runs_version_only_when_completed forbids a run that
    # claims a version while it is still materialising.
    run.profile_version_id = version_id
    run.finished_at = datetime.now(timezone.utc)
    advance(run, STATUS_COMPLETED)
    db.commit()
    logger.info(
        "resume.run.completed",
        extra={
            "resume_run_id": run.id,
            "resume_document_id": run.resume_document_id,
            "attempt": run.attempt,
            "profile_version_id": version_id,
            "page_count": run.page_count,
        },
    )
    return run


def fail_run(db: Session, run: ResumeProcessingRun, failure: ProcessingFailed) -> None:
    run.failed_stage = failure.stage
    run.error_code = failure.code
    run.error_message = failure.message
    run.finished_at = datetime.now(timezone.utc)
    advance(run, STATUS_FAILED)
    logger.warning(
        "resume.run.failed",
        extra={
            "resume_run_id": run.id,
            "resume_document_id": run.resume_document_id,
            "attempt": run.attempt,
            "stage": failure.stage,
            "error_code": failure.code,
            "classification": classification(failure.code),
            "retryable": is_retryable(failure.code),
        },
    )


def _abort(db: Session, run_id: int) -> None:
    """Last resort: end a run that stopped for a reason we did not model.

    Takes an id rather than the object because the session may be poisoned: the
    rollback below is what makes it usable again, and it expires whatever was
    loaded before it. Failing to record the failure is logged and swallowed —
    the original exception is the one worth propagating.
    """
    try:
        db.rollback()
        run = db.get(ResumeProcessingRun, run_id)
        if run is None or run.is_terminal:
            return
        _fail(
            db,
            run,
            ProcessingFailed(
                STAGE_FOR_STATUS.get(run.status, "validate"),
                "unexpected_error",
                "Processing stopped unexpectedly.",
            ),
        )
        db.commit()
    except Exception:
        logger.exception("could not record the terminal failure for run %s", run_id)
        db.rollback()


def _run_validate_and_extract(
    db: Session,
    run: ResumeProcessingRun,
    document: ResumeDocument,
    storage: StorageBackend,
    ocr_engine,
):
    advance(run, STATUS_VALIDATING)
    db.flush()

    with _stage(db, run, "validate"):
        try:
            data = storage.get(document.storage_key)
        except ObjectNotFound as exc:
            raise ProcessingFailed(
                "validate", "stored_object_missing", "The stored document could not be read."
            ) from exc

        try:
            # Re-validated from storage rather than trusted from upload: the
            # bytes are the only evidence, and this run may be a retry days later.
            validate_upload(
                filename=document.original_filename,
                declared_media_type=document.content_type,
                data=data,
            )
        except FileValidationError as exc:
            raise ProcessingFailed("validate", exc.code, exc.message) from exc

    advance(run, STATUS_EXTRACTING)
    db.flush()

    with _stage(db, run, "extract"):
        try:
            extracted = extract(data, media_type=document.content_type)
        except ExtractionError as exc:
            raise ProcessingFailed("extract", exc.code, exc.message) from exc

    # OCR means rasterising pages, and only a PDF has pages to rasterise. A DOCX
    # that yielded too little text has nothing to re-read — routing it here fed
    # DOCX bytes to a PDF renderer, which fails at a stage the user cannot act
    # on. It falls through to the no_text_extracted check below instead, which
    # is the honest answer for a Word file we could not read.
    ocr_pages = (
        pages_needing_ocr(extracted, min_chars=settings.RESUME_OCR_MIN_CHARS_PER_PAGE)
        if document.content_type == PDF_MEDIA_TYPE
        else []
    )

    ocr_available = bool(ocr_pages) and ocr_engine.is_available()
    ocred: list[int] = []
    attempted_ocr = False

    if ocr_pages and not ocr_available and not must_fail_without_ocr(extracted, ocr_pages):
        # A minority of sparse pages — a references line, a cover page — on a
        # resume whose other pages read fine. Failing here refused a readable
        # resume over one near-empty page, permanently, since only installing
        # an engine could change the answer. The run continues on the pages it
        # has; the ones it could not read are flagged, persisted as such, and
        # named on the review screen, so nothing is lost without a word.
        #
        # The status never enters ocr_required: extracting -> structuring is a
        # legal move, and ocr_required -> structuring is not. The stage is
        # still on record, as `skipped`, with the page numbers (never text).
        extracted = mark_unread(extracted, ocr_pages, reason=UNREAD_OCR_UNAVAILABLE)
        _record(
            db,
            run,
            stage="ocr",
            outcome="skipped",
            detail={"code": "ocr_unavailable", "unread_pages": list(ocr_pages)},
        )
    elif ocr_pages:
        advance(run, STATUS_OCR_REQUIRED)
        db.flush()

        with _stage(db, run, "ocr"):
            if not ocr_available:
                # A hard failure, not empty pages, when the pages OCR cannot
                # reach carry most of the document: empty pages would produce
                # a confidently empty resume the user might then confirm. See
                # `extraction.MAX_UNREAD_PAGE_SHARE`.
                raise ProcessingFailed(
                    "ocr",
                    "ocr_unavailable",
                    "This document needs OCR, but no OCR engine is available.",
                )

            advance(run, STATUS_OCR_PROCESSING)
            db.flush()
            attempted_ocr = True
            try:
                extracted = apply_ocr(
                    extracted,
                    source=data,
                    page_numbers=ocr_pages,
                    engine=ocr_engine,
                )
            except OcrError as exc:
                raise ProcessingFailed("ocr", exc.code, exc.message) from exc
            ocred = [
                page.page_number for page in extracted.pages if page.method == METHOD_OCR
            ]

    _persist_pages(db, run, extracted)
    run.page_count = extracted.page_count
    run.extracted_char_count = extracted.total_chars
    # Pages actually read by OCR — not pages that wanted it. A page skipped for
    # want of an engine, or for being too large to render, was not OCR'd.
    run.ocr_page_count = len(ocred)
    db.flush()

    if not extracted.total_chars:
        # Recorded against the stage that actually produced nothing, so the
        # failure points at OCR when OCR was the last thing to try.
        raise ProcessingFailed(
            "ocr" if attempted_ocr else "extract",
            "no_text_extracted",
            "No readable text could be found in the document.",
        )

    return extracted


#: What the review screen says about a page nobody read, by reason. Our words,
#: from a closed vocabulary — never a library's, which would quote the page.
_UNREAD_REASONS = {
    UNREAD_OCR_UNAVAILABLE: (
        "This page has almost no machine-readable text, so it may be a scanned "
        "image, and no OCR engine is available to read it. Anything on it that "
        "is not text was left out — check it against your file."
    ),
    UNREAD_TOO_LARGE_TO_RENDER: (
        "This page is too large to render for OCR, so anything on it that is "
        "not text was left out — check it against your file."
    ),
    UNREAD_PAGE_LIMIT: (
        "The document has more pages than a resume is read to, so these pages "
        "were not read."
    ),
}


def unreadable_pages_reported(extracted) -> list[UnmappedItem]:
    """One review-screen entry per page this run could not fully read.

    A run that continues past an unread page must say so where the person
    reviewing the draft will look — the same `unmapped` list that already
    carries items the profile could not hold — or "continue" would just be a
    quieter way of losing the page.
    """
    return [
        UnmappedItem(
            section="pages",
            reason=_UNREAD_REASONS.get(reason, "This page could not be read."),
            summary=which,
        )
        for which, reason in unread_pages(extracted)
    ]


def _persist_pages(db: Session, run: ResumeProcessingRun, extracted) -> None:
    for page in extracted.pages:
        # Links ride in the metadata column rather than in `text`: they are part
        # of the page's evidence, but counting them as text would let a scanned
        # page with one link annotation dodge the OCR threshold.
        metadata = dict(page.metadata or {})
        if page.links:
            metadata["links"] = [link.as_dict() for link in page.links]
        db.add(
            ResumeDocumentPage(
                resume_processing_run_id=run.id,
                page_number=page.page_number,
                text=page.text,
                char_count=page.char_count,
                extraction_method=page.method,
                extraction_metadata=metadata or None,
            )
        )


def _run_structuring(db: Session, run: ResumeProcessingRun, extracted, structurer):
    advance(run, STATUS_STRUCTURING)
    db.flush()

    with _stage(db, run, "structure"):
        try:
            result = structurer.structure(extracted)
        except ProviderError as exc:
            # The provider's own vocabulary is already this domain's vocabulary
            # — failures.py classifies these codes — so it travels unchanged
            # rather than being flattened into one "AI failed" code that
            # nothing could make a retry decision from.
            raise ProcessingFailed("structure", exc.code, exc.message) from exc
        except StructuringError as exc:
            raise ProcessingFailed("structure", exc.code, exc.message) from exc
        except Exception as exc:  # an adapter that misbehaves is still our failure
            logger.exception("structurer %s raised", getattr(structurer, "name", "?"))
            raise ProcessingFailed(
                "structure", "structuring_failed", "The resume could not be interpreted."
            ) from exc

    verify_provenance(result.candidate, extracted)
    return result


def _run_result_validation(db: Session, run: ResumeProcessingRun, reading, extracted):
    """Map the reading onto the snapshot, and gather everything left out.

    Three sources, one list, in the order a reader meets them: pages that could
    not be read at all, content the structurer read and had nowhere to put
    (including any hyperlink it placed nowhere), and items the snapshot could
    not hold. They are reported together because the review screen shows them
    together — and because the rule is the same for all three: never dropped
    without a word.
    """
    advance(run, STATUS_VALIDATING_RESULT)
    db.flush()

    with _stage(db, run, "validate_result"):
        try:
            snapshot, unmapped = mapping.to_snapshot(reading.candidate)
        except Exception as exc:
            logger.exception("candidate could not be mapped onto the snapshot")
            raise ProcessingFailed(
                "validate_result",
                "result_schema_invalid",
                "The extracted data did not fit the profile schema.",
            ) from exc

    reported = [
        *unreadable_pages_reported(extracted),
        *reading.unmapped,
        *unplaced_links(reading.candidate, extracted),
        *unmapped,
    ]
    return snapshot, reported


def _run_materialise(
    db: Session,
    run: ResumeProcessingRun,
    document: ResumeDocument,
    snapshot,
    unmapped,
    reading,
) -> int:
    advance(run, STATUS_MATERIALISING)
    db.flush()

    with _stage(db, run, "materialise"):
        # Everything needed to account for this reading later: which structurer
        # ran, through which provider and model, under which prompt, against
        # which schema, and what the call cost. Written from the result itself
        # rather than read off the structurer object, so it describes the call
        # that actually happened rather than the adapter's current settings.
        db.add(
            ResumeStructuredResult(
                resume_processing_run_id=run.id,
                structurer=reading.structurer,
                provider=reading.provider,
                model=reading.model,
                schema_version=reading.schema_version,
                prompt_version=reading.prompt_version,
                provider_metadata=reading.metadata or None,
                payload=reading.candidate.model_dump(mode="json"),
                unmapped=[item.model_dump(mode="json") for item in unmapped] or None,
            )
        )
        db.flush()

        # Commit before handing over to the profile service. Its functions own
        # their own transaction and roll it back on failure — and a rollback on
        # this session discards everything this run has written but not yet
        # committed: its pages, its events, its counts. Committing first means a
        # failed draft costs us the draft, not the record of the whole run.
        db.commit()

        try:
            profile_service.get_profile(db, user_id=document.user_id)
        except profile_service.NotFoundError:
            # A resume can be uploaded before a profile exists; the draft needs
            # one, so create it rather than failing the run.
            profile_service.create_profile(db, user_id=document.user_id)

        try:
            version = profile_service.create_draft_version_with_snapshot(
                db,
                user_id=document.user_id,
                snapshot=snapshot,
            )
        except Exception as exc:
            logger.exception("materialising a draft profile version failed")
            raise ProcessingFailed(
                "materialise",
                "draft_creation_failed",
                "The extracted data could not be saved as a profile draft.",
            ) from exc

    return version.id
