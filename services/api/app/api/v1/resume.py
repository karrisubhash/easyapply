"""Resume routes.

Thin, like the profile routes: take the session and the caller, call one
service function, serialise. The one piece of logic here is the upload size
guard, which has to live at the HTTP edge because the point is to stop reading
before the whole body is in memory.
"""

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, ProcessingQueueDep
from app.api.identity import CurrentUser
from app.core.config import settings
from app.models import ResumeStructuredResult
from app.schemas.resume import (
    ProcessingResultRead,
    ProcessingRunRead,
    ResumeDocumentDetail,
    ResumeDocumentRead,
    StructuredResultRead,
    structured_result_payload,
)
from app.services.errors import NotFoundError
from app.services.resume import retry, service

router = APIRouter(tags=["resumes"], prefix="/resumes")

# Read in chunks so an oversized upload is refused after one chunk over the
# limit rather than after the whole thing has been buffered.
_CHUNK = 64 * 1024


async def _read_limited(upload: UploadFile) -> bytes:
    limit = settings.RESUME_MAX_UPLOAD_BYTES
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "detail": f"The file is larger than the {limit // (1024 * 1024)} MB limit.",
                    "code": "file_too_large",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _document_read(db, document) -> ResumeDocumentRead:
    run = service.latest_run(db, document=document)
    return ResumeDocumentRead(
        id=document.id,
        original_filename=document.original_filename,
        content_type=document.content_type,
        byte_size=document.byte_size,
        created_at=document.created_at,
        latest_run=ProcessingRunRead.model_validate(run) if run else None,
    )


@router.post("", response_model=ResumeDocumentRead)
async def upload_resume(
    db: DbSession,
    user: CurrentUser,
    queue: ProcessingQueueDep,
    file: UploadFile = File(...),
):
    """Accept a resume, store it, and queue it for processing.

    Returns as soon as the artifact is durable and a processing run exists. The
    pipeline — extraction, OCR, a model call — happens on a worker, so how long
    a resume takes to read has no bearing on how long this request takes to
    answer.

    Returns 201 for a new document and 200 when these exact bytes were already
    uploaded by this user, which makes a retried request idempotent rather than
    duplicating the resume.
    """
    data = await _read_limited(file)

    # This handler has to be async — the size guard above streams the body — but
    # everything below it is blocking: hashing, disk and database. Run on the
    # event loop it would stop the process from serving any other request. The
    # threadpool is what a plain `def` endpoint would have given us for free.
    try:
        document, created = await run_in_threadpool(
            service.upload_resume,
            db,
            user_id=user.id,
            filename=file.filename,
            declared_media_type=file.content_type,
            data=data,
        )
    except service.UploadRejected as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.message, "code": exc.code},
        )

    # Only a new document opens a run. Re-uploading the same bytes resolves to
    # the existing document and enqueues nothing, so a client retrying after a
    # timeout cannot start a second pipeline over one resume — the uniqueness
    # constraint on (user_id, content_sha256) is what makes that true, and this
    # branch is the only place it has to be honoured.
    if created:
        await run_in_threadpool(service.start_run, db, document=document, queue=queue)

    payload = await run_in_threadpool(_document_read, db, document)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=payload.model_dump(mode="json"),
    )


@router.get("", response_model=list[ResumeDocumentRead])
def list_resumes(db: DbSession, user: CurrentUser):
    return [_document_read(db, doc) for doc in service.list_documents(db, user_id=user.id)]


@router.get("/{document_id}", response_model=ResumeDocumentDetail)
def read_resume(document_id: int, db: DbSession, user: CurrentUser):
    document = service.get_document(db, user_id=user.id, document_id=document_id)
    base = _document_read(db, document)
    run = service.latest_run(db, document=document)
    return ResumeDocumentDetail(
        **base.model_dump(),
        events=run.events if run else [],
    )


@router.get("/{document_id}/result", response_model=ProcessingResultRead)
def read_result(document_id: int, db: DbSession, user: CurrentUser):
    """The structured reading, for the review screen."""
    document = service.get_document(db, user_id=user.id, document_id=document_id)
    run = service.latest_run(db, document=document)
    if run is None:
        raise NotFoundError("This resume has not been processed yet.")

    stored = db.query(ResumeStructuredResult).filter_by(resume_processing_run_id=run.id).one_or_none()

    result = None
    if stored is not None:
        version_number = None
        if run.profile_version_id is not None:
            from app.models import ProfileVersion

            version = db.get(ProfileVersion, run.profile_version_id)
            version_number = version.version_number if version else None

        result = StructuredResultRead(
            structurer=stored.structurer,
            model=stored.model,
            candidate=structured_result_payload(stored.payload),
            unmapped=stored.unmapped or [],
            pages=run.pages,
            profile_version_number=version_number,
        )

    return ProcessingResultRead(
        document=_document_read(db, document),
        run=ProcessingRunRead.model_validate(run),
        result=result,
    )


@router.post("/{document_id}/reprocess", response_model=ResumeDocumentRead)
def reprocess(
    document_id: int, db: DbSession, user: CurrentUser, queue: ProcessingQueueDep
):
    """Try again. Always a new run, never a reopened one.

    Refused with a 409 when the previous attempt is still in flight, or when it
    failed for a reason the same bytes will reproduce.
    """
    document = service.get_document(db, user_id=user.id, document_id=document_id)
    service.require_retryable(service.latest_run(db, document=document))

    service.start_run(db, document=document, queue=queue)
    return _document_read(db, document)
