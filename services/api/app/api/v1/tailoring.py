"""Tailoring routes.

Thin, like every other router here: take the session and the caller, call one
service function, serialise. Ownership is never checked in this file — it is
enforced inside the service layer, on every query, because a router that forgets
is a data leak while a service that forgets is a test failure.

Two shapes, plus the file:

    POST /jobs/{job_id}/tailored-resumes  start a run. 201 for a new one, 200
                                          when one for the same job and version
                                          is already in flight.
    GET  /tailored-resumes                every run this caller owns.
    GET  /jobs/{job_id}/tailored-resumes  the ones for this job.
    GET  /tailored-resumes/{id}           one run in full, with the document.
    GET  /tailored-resumes/{id}/artifact  the file. The ONLY route to bytes.

The artifact route is the one handler in this application that answers with
something other than JSON, and it is the one place ownership failing would be
worst. It reaches the bytes through `service.get_artifact`, which resolves the
run against the caller before it looks at storage at all — so a changed id in
the URL is a 404 from the run lookup, long before any key is read.
"""

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, TailoringQueueDep
from app.api.identity import CurrentUser
from app.models import ProfileVersion
from app.models.tailoring import TailoringRun
from app.schemas.tailored_resume import TailoredResumeDocument
from app.schemas.tailoring import (
    ArtifactRead,
    TailoredResumeRead,
    TailoringIssueRead,
    TailoringRequest,
    TailoringRunDetail,
    TailoringRunSummary,
    tailored_document,
)
from app.services.tailoring import service

router = APIRouter(tags=["tailoring"])


def _summary_fields(db, run: TailoringRun) -> dict:
    """The derived fields a listing needs, gathered in one place.

    `can_retry` and `is_stalled` are computed by the service rather than by the
    schema: both read configuration and the clock, and a client — or a second
    copy in Pydantic — re-deriving them would be free to disagree with the
    server that actually enforces them.
    """
    tailored = run.result
    version = db.get(ProfileVersion, run.profile_version_id)

    return {
        "is_stalled": service.is_stalled(run),
        "can_retry": service.can_retry(run),
        "profile_version_number": version.version_number if version else None,
        "job_title": run.job.job_title if run.job else None,
        "company_name": run.job.company_name if run.job else None,
        "grounding_status": tailored.grounding_status if tailored else None,
        "error_count": tailored.error_count if tailored else None,
        "warning_count": tailored.warning_count if tailored else None,
        "has_artifact": bool(tailored and tailored.artifact),
    }


def _summary(db, run: TailoringRun) -> TailoringRunSummary:
    # model_copy rather than a second constructor call: the derived fields are
    # server-computed and already correct, and re-validating them would only
    # give a chance for a default to win over a real value.
    return TailoringRunSummary.model_validate(run).model_copy(update=_summary_fields(db, run))


def _detail(db, run: TailoringRun) -> TailoringRunDetail:
    tailored = run.result

    result = None
    artifact = None
    if tailored is not None:
        result = TailoredResumeRead(
            document=tailored_document(tailored.payload),
            schema_version=tailored.schema_version,
            grounding_status=tailored.grounding_status,
            error_count=tailored.error_count,
            warning_count=tailored.warning_count,
            issues=[TailoringIssueRead.model_validate(issue) for issue in tailored.issues],
            created_at=tailored.created_at,
        )
        if tailored.artifact is not None:
            artifact = ArtifactRead.model_validate(tailored.artifact)

    # The summary is built first and widened, rather than validating the run
    # against TailoringRunDetail directly: `result` on the ORM row is a
    # TailoredResume holding a JSONB payload, and the response field of the same
    # name is the re-validated document. They are different objects with the
    # same name, so from_attributes must not be let near it.
    summary = _summary(db, run)
    return TailoringRunDetail(
        **summary.model_dump(exclude={"is_terminal"}),
        result=result,
        artifact=artifact,
    )


@router.post("/jobs/{job_id}/tailored-resumes", response_model=TailoringRunDetail)
def start_tailoring(
    job_id: int,
    payload: TailoringRequest,
    db: DbSession,
    user: CurrentUser,
    queue: TailoringQueueDep,
):
    """Generate a resume for this job from a confirmed profile version.

    Returns as soon as the run is durable. Generation, grounding validation and
    rendering happen on a worker, so how long a model takes has no bearing on
    how long this request takes to answer.

    201 when a run was opened, 200 when one for the same job and version is
    already in flight — which makes a double-click resolve to the run that is
    already going rather than racing a second one against it.

    409 when the job has not been structured yet, or when the named version is a
    draft: both are states where a tailored resume would be meaningless rather
    than merely empty.
    """
    run, created = service.start_tailoring(
        db,
        user_id=user.id,
        job_id=job_id,
        profile_version_id=payload.profile_version_id,
        match_result_id=payload.match_result_id,
        queue=queue,
    )
    db.refresh(run)
    body = _detail(db, run)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=body.model_dump(mode="json"),
    )


@router.get("/jobs/{job_id}/tailored-resumes", response_model=list[TailoringRunSummary])
def list_for_job(job_id: int, db: DbSession, user: CurrentUser):
    """This caller's tailored resumes for this job, newest first."""
    return [_summary(db, run) for run in service.list_runs(db, user_id=user.id, job_id=job_id)]


@router.get("/tailored-resumes", response_model=list[TailoringRunSummary])
def list_all(db: DbSession, user: CurrentUser):
    """Every tailored resume this caller owns, newest first."""
    return [_summary(db, run) for run in service.list_runs(db, user_id=user.id)]


@router.get("/tailored-resumes/{run_id}", response_model=TailoringRunDetail)
def read_one(run_id: int, db: DbSession, user: CurrentUser):
    """One run in full: the document, every grounding finding, the artifact."""
    return _detail(db, service.get_run(db, user_id=user.id, run_id=run_id))


@router.get(
    "/tailored-resumes/{run_id}/artifact",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {}
            },
            "description": "The generated resume.",
        }
    },
)
def download_artifact(run_id: int, db: DbSession, user: CurrentUser):
    """The generated file.

    Everything that decides whether this is allowed lives in
    `service.get_artifact` — ownership, completion, the grounding verdict and
    whether the object is actually in storage. This handler's only job is to
    attach the filename and the content type the artifact row recorded.

    `Content-Disposition` carries a filename built server-side from the job and
    the profile version, sanitised at the point it is generated
    (`rendering.build_filename`). It is never echoed from client input.
    """
    data, filename, media_type = service.get_artifact(db, user_id=user.id, run_id=run_id)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # The bytes are one user's personal data behind an identity check.
            # Nothing in front of this may keep a copy.
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["router", "TailoredResumeDocument"]
