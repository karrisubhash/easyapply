"""Job routes.

Thin, like the profile and resume routes: take the session and the caller,
call one service function, serialise the result. No business logic here —
validation lives in the Pydantic request schema, normalization and
deduplication live in the service layer, structuring lives in the pipeline.

The URL space carries no identity beyond what `CurrentUser` already resolves:
`/jobs` means "the caller's jobs" under a dev header exactly as it will under a
bearer token, and every job is scoped to its owner in the service layer, so a
wrong id is a 404 inside your own jobs rather than a peek at someone else's.
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.api.identity import CurrentUser
from app.schemas.job import (
    JobDetail,
    JobIngestionRequest,
    JobRequirementsRead,
    JobRequirementsResponse,
    JobSummary,
    job_requirements_payload,
)
from app.services.job import service

router = APIRouter(tags=["jobs"], prefix="/jobs")


@router.post("", response_model=JobDetail)
def create_job(payload: JobIngestionRequest, db: DbSession, user: CurrentUser):
    """Ingest a job posting.

    Returns 201 for a new job — structured and ready to review in the same
    response — and 200 when this exact identity already existed, unchanged.
    Idempotent for the same reason resume upload is: a client retrying after a
    timeout must not create a second copy of what it already submitted.
    """
    job, created = service.ingest_job(db, user_id=user.id, payload=payload)
    body = JobDetail.model_validate(job)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=body.model_dump(mode="json"),
    )


@router.get("", response_model=list[JobSummary])
def list_jobs(db: DbSession, user: CurrentUser):
    return service.list_jobs(db, user_id=user.id)


@router.get("/{job_id}", response_model=JobDetail)
def read_job(job_id: int, db: DbSession, user: CurrentUser):
    return service.get_job(db, user_id=user.id, job_id=job_id)


@router.get("/{job_id}/requirements", response_model=JobRequirementsResponse)
def read_requirements(job_id: int, db: DbSession, user: CurrentUser):
    """The structured reading, for the review screen.

    `requirements` is null until a run has completed — a job just ingested and
    still processing, or one whose only attempt failed, has a `run` to show
    and nothing structured yet. Never an error: absence of a result is not the
    same fact as failure to produce one, and the run itself already says which
    is true.
    """
    job = service.get_job(db, user_id=user.id, job_id=job_id)
    # The run that produced the newest reading — the one matching and tailoring
    # use — not necessarily the latest attempt. After a failed reprocess the two
    # differ, and `job.latest_run` (below) still reports the failed attempt; see
    # `service.get_requirements_run`. (Kept out of the docstring above, which is
    # the published OpenAPI description the shared client is generated from.)
    run = service.get_requirements_run(db, job=job)

    requirements = None
    if run is not None and run.requirements is not None:
        stored = run.requirements
        requirements = JobRequirementsRead(
            structurer=stored.structurer,
            provider=stored.provider,
            model=stored.model,
            prompt_version=stored.prompt_version,
            schema_version=stored.schema_version,
            candidate=job_requirements_payload(stored.payload),
            created_at=stored.created_at,
        )

    return JobRequirementsResponse(
        job=JobSummary.model_validate(job),
        run=run,
        requirements=requirements,
    )


@router.post("/{job_id}/reprocess", response_model=JobDetail)
def reprocess(job_id: int, db: DbSession, user: CurrentUser):
    """Try structuring again. Always a new run, never a reopened one — the
    same discipline resume reprocessing uses, for the same reason: rewriting a
    finished attempt would destroy the record of what it actually produced."""
    job = service.get_job(db, user_id=user.id, job_id=job_id)
    service.reprocess(db, job=job)
    db.refresh(job)
    return job
