"""Match routes.

Thin, like every other router here: take the session and the caller, call one
service function, serialise. Ownership is never checked in this file — it is
enforced inside the service layer, on every query, because a router that
forgets is a data leak while a service that forgets is a test failure.

Two shapes, deliberately:

    POST /jobs/{job_id}/matches   create-or-return. A repeated request for the
                                  same (profile version, requirements row,
                                  algorithm) answers 200 with the existing
                                  result rather than 201 with a second one.
    GET  /matches/{match_id}      the result in full, by its own id.

The nested collection reads "this job's matches for me", which is the only
listing the UI needs; a global /matches listing is not offered because nothing
asks for it yet.
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.api.identity import CurrentUser
from app.schemas.match import MatchDetail, MatchRequest, MatchSummary
from app.services.matching import service

router = APIRouter(tags=["matches"])


@router.post("/jobs/{job_id}/matches", response_model=MatchDetail)
def create_match(job_id: int, payload: MatchRequest, db: DbSession, user: CurrentUser):
    """Match a confirmed profile version against this job's requirements.

    201 when the evaluation is new, 200 when this exact evaluation already
    existed — idempotent for the same reason job ingestion is: a client
    retrying after a timeout must not create a second copy of the same
    judgment.

    409 when the job has not been structured yet, or when the named version is
    a draft: both are states where a match would be meaningless rather than
    merely empty.
    """
    match, created = service.create_match(
        db,
        user_id=user.id,
        job_id=job_id,
        profile_version_id=payload.profile_version_id,
    )
    body = MatchDetail.model_validate(match)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=body.model_dump(mode="json"),
    )


@router.get("/jobs/{job_id}/matches", response_model=list[MatchSummary])
def list_matches(job_id: int, db: DbSession, user: CurrentUser):
    """This caller's matches for this job, newest first."""
    return service.list_matches_for_job(db, user_id=user.id, job_id=job_id)


@router.get("/matches/{match_id}", response_model=MatchDetail)
def read_match(match_id: int, db: DbSession, user: CurrentUser):
    """One match in full, with every requirement verdict and its evidence."""
    return service.get_match(db, user_id=user.id, match_id=match_id)
