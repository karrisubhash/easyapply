"""The resume application service.

Owns upload, deduplication and starting a run. The pipeline itself lives in
pipeline.py; this module is what the API calls.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ResumeDocument, ResumeProcessingRun
from app.services.errors import ConflictError, NotFoundError
from app.services.resume import retry
from app.services.resume.validation import FileValidationError, validate_upload
from app.services.storage import (
    StorageBackend,
    build_storage_key,
    content_digest,
    get_storage,
)

logger = logging.getLogger(__name__)


class UploadRejected(Exception):
    """The bytes are not something we will accept. Carries the validator's code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def upload_resume(
    db: Session,
    *,
    user_id: int,
    filename: str | None,
    declared_media_type: str | None,
    data: bytes,
    storage: StorageBackend | None = None,
) -> tuple[ResumeDocument, bool]:
    """Store an uploaded resume. Returns the document and whether it is new.

    Duplicate behaviour: identical bytes from the same user resolve to the
    document that already exists, and the caller reports 200 rather than 201.
    This is what makes the endpoint idempotent — a client that retries after a
    network timeout gets the same document rather than a second copy of its own
    resume — and it is safe because the artifact is immutable, so "the same
    content" really is "the same document".

    Note that 200 says nothing about processing: the existing document's latest
    run may have failed, and the caller decides whether to start another.
    """
    storage = storage or get_storage()

    try:
        validated = validate_upload(
            filename=filename, declared_media_type=declared_media_type, data=data
        )
    except FileValidationError as exc:
        raise UploadRejected(exc.code, exc.message) from exc

    digest = content_digest(data)

    existing = db.scalar(
        select(ResumeDocument).where(
            ResumeDocument.user_id == user_id,
            ResumeDocument.content_sha256 == digest,
        )
    )
    if existing is not None:
        return existing, False

    key = build_storage_key(
        user_id=user_id, content_sha256=digest, extension=validated.extension
    )

    # Bytes first, then the row. The failure mode is then an orphaned object
    # rather than a committed row pointing at nothing — and because the key is
    # content-addressed, a retry overwrites that orphan with identical bytes.
    storage.put(key, data, content_type=validated.media_type)

    document = ResumeDocument(
        user_id=user_id,
        original_filename=validated.filename,
        content_type=validated.media_type,
        byte_size=validated.byte_size,
        content_sha256=digest,
        storage_key=key,
    )
    db.add(document)

    # A concurrent upload of the same bytes can still lose the race between the
    # SELECT above and this INSERT. The savepoint is what makes recovery
    # possible: a constraint violation aborts the transaction, and nothing else
    # could be read on this session afterwards without it.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        duplicate = db.scalar(
            select(ResumeDocument).where(
                ResumeDocument.user_id == user_id,
                ResumeDocument.content_sha256 == digest,
            )
        )
        if duplicate is None:
            raise
        return duplicate, False

    db.commit()
    return document, True


def start_run(
    db: Session, *, document: ResumeDocument, queue=None
) -> ResumeProcessingRun:
    """Open the next processing attempt and offer it for processing.

    Retry is always a NEW run. Reopening a failed one would make the state
    graph cyclic and destroy the record of what went wrong last time.

    The run is committed before it is enqueued, and never the other way round: a
    worker that picks up an id whose row is not visible yet would find nothing
    and treat it as gone. Committed-then-enqueued means the worst case is a lost
    message, and a lost message costs latency rather than work — the run is
    still outstanding in the table for the reaper or a poll to find.

    A person asking for an attempt never waits: no backoff is applied here. The
    backoff paces a machine retrying on its own, and a person clicking the
    button has already supplied the delay.
    """
    run = retry.open_next_attempt(db, document=document)
    if run is None:
        # Two requests raced for the same attempt number and this one lost. The
        # other created the run, so the truthful answer is the same one
        # require_retryable gives for a run already in flight.
        raise ConflictError("This resume is already being processed.")

    if queue is not None:
        queue.enqueue(run.id)
    return run


def get_document(db: Session, *, user_id: int, document_id: int) -> ResumeDocument:
    """Fetch a document the caller owns, or 404.

    Scoped by user in the query rather than checked afterwards, so a document
    belonging to someone else is indistinguishable from one that does not exist.
    """
    document = db.scalar(
        select(ResumeDocument).where(
            ResumeDocument.id == document_id,
            ResumeDocument.user_id == user_id,
        )
    )
    if document is None:
        raise NotFoundError("That resume does not exist.")
    return document


def list_documents(db: Session, *, user_id: int) -> list[ResumeDocument]:
    return list(
        db.scalars(
            select(ResumeDocument)
            .where(ResumeDocument.user_id == user_id)
            .order_by(ResumeDocument.created_at.desc())
        )
    )


def latest_run(db: Session, *, document: ResumeDocument) -> ResumeProcessingRun | None:
    return db.scalar(
        select(ResumeProcessingRun)
        .where(ResumeProcessingRun.resume_document_id == document.id)
        .order_by(ResumeProcessingRun.attempt.desc())
        .limit(1)
    )


def require_retryable(run: ResumeProcessingRun | None) -> None:
    """Refuse a manual retry that cannot help.

    Two refusals, both 409. A run still in flight — starting a second would
    duplicate work the lease would then have to arbitrate over. And a failure
    the same bytes will reproduce: unsupported format, corrupt file, a
    password-protected PDF. Telling the user to fix the input is more use than
    letting them watch it fail again.

    A failure this deployment caused — a scan with no OCR engine installed, a
    revoked provider key — is NOT refused: re-uploading the same bytes returns
    this same document, so a refusal would leave it unprocessable forever, even
    after the deployment is fixed. See `failures.DEPLOYMENT_ERROR_CODES`.
    """
    reason = retry.manual_retry_refusal(run)
    if reason is not None:
        raise ConflictError(reason)
