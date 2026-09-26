"""Profile routes.

Every handler is the same three moves: take the session and the caller, call
one service function, serialise the result. No business logic, no transaction
management, no error translation — those live in the service layer and in
app/api/errors.py respectively.

The URL space carries no identity: /profile means "the caller's profile"
under a dev header exactly as it will under a bearer token. Versions are
addressed by version_number, which is unique per profile and already
user-visible, so cross-tenant addressing is unrepresentable rather than merely
forbidden.
"""

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.api.identity import CurrentUser
from app.schemas.profile import (
    ConfirmVersionRequest,
    ProfileRead,
    ProfileVersionRead,
    ProfileVersionSummary,
)
from app.schemas.snapshot import Snapshot
from app.services import profile_service

router = APIRouter(tags=["profile"])


@router.post("/profile", response_model=ProfileRead, status_code=status.HTTP_201_CREATED)
def create_profile(db: DbSession, user: CurrentUser):
    return profile_service.create_profile(db, user_id=user.id)


@router.get("/profile", response_model=ProfileRead)
def read_profile(db: DbSession, user: CurrentUser):
    return profile_service.get_profile(db, user_id=user.id)


@router.post(
    "/profile/versions",
    response_model=ProfileVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_draft_version(db: DbSession, user: CurrentUser):
    return profile_service.create_draft_version(db, user_id=user.id)


@router.get("/profile/versions", response_model=list[ProfileVersionSummary])
def list_versions(db: DbSession, user: CurrentUser):
    return profile_service.list_versions(db, user_id=user.id)


@router.get("/profile/versions/current", response_model=ProfileVersionRead)
def read_current_version(db: DbSession, user: CurrentUser):
    return profile_service.get_current_version(db, user_id=user.id)


@router.get("/profile/versions/{version_number}", response_model=ProfileVersionRead)
def read_version(version_number: int, db: DbSession, user: CurrentUser):
    return profile_service.get_version(db, user_id=user.id, version_number=version_number)


@router.put(
    "/profile/versions/{version_number}/snapshot",
    response_model=ProfileVersionRead,
)
def replace_snapshot(
    version_number: int, snapshot: Snapshot, db: DbSession, user: CurrentUser
):
    return profile_service.replace_snapshot(
        db, user_id=user.id, version_number=version_number, snapshot=snapshot
    )


@router.post(
    "/profile/versions/{version_number}/confirm",
    response_model=ProfileVersionRead,
)
def confirm_version(
    version_number: int,
    payload: ConfirmVersionRequest,
    db: DbSession,
    user: CurrentUser,
):
    return profile_service.confirm_version(
        db,
        user_id=user.id,
        version_number=version_number,
        make_current=payload.make_current,
    )


@router.post(
    "/profile/versions/{version_number}/make-current",
    response_model=ProfileVersionRead,
)
def make_version_current(version_number: int, db: DbSession, user: CurrentUser):
    """Make an already-confirmed version the current one. 409 for a draft."""
    return profile_service.make_version_current(
        db, user_id=user.id, version_number=version_number
    )
