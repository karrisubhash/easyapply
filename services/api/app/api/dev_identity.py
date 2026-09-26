"""The development identity: `AUTH_MODE=development` (the default).

One of the two modes of the identity seam, app/api/identity.py — the other is
the real sign-in, Microsoft Entra External ID (app/api/assertion_identity.py).
Routes depend on `app.api.identity.CurrentUser`; this module is what it calls in
development, and what the test suites act as two people through.

The service layer takes user_id: int and has no concept of authentication at
all, which is what keeps the blast radius to this module.

WHAT THIS IS, AND WHERE IT MUST NOT RUN
---------------------------------------
There is no authentication here. The caller's identity is whatever the
X-Dev-User-Email header says, so anyone who can reach the API can be anyone by
typing their address. That is tolerable on a developer's machine and nowhere
else, so the seam refuses to operate when ENVIRONMENT=production:

    ENVIRONMENT=development|test  -> header, else DEV_USER_EMAIL, as below.
    ENVIRONMENT=production        -> 401 on EVERY request, before the header
                                     is read or the database touched.

Refusing per request rather than failing startup is deliberate. The check sits
in the only place identity is decided, so no way of assembling the application
— another entry point, a test harness, a router mounted elsewhere, settings
changed after import — can route a request around it. It also leaves what does
not authenticate anybody working: `/health`, migrations and the resume worker
(which never imports this module) all run in production as they did. And a
deployment that forgot real sign-in is not "up with a hole", it is up and
refusing, with an error in the log for every request that says why.

WHAT A USABLE HEADER IS
-----------------------
The header is attacker-chosen text going into a unique VARCHAR(255) column, so
it is checked before it reaches the database:

    absent                 -> DEV_USER_EMAIL (what a developer's curl gets)
    present but blank      -> 401. Not the default user, and never a user
                              whose email is "".
    longer than the column -> 401, not a DataError answered as a 500.
    control characters     -> 401. Nothing legitimate contains one, and a NUL
                              is another DataError.

Every refusal is the same fixed sentence and none of them echoes the value.

A BEARER TOKEN IS A MODE MISMATCH, NOT A CREDENTIAL
---------------------------------------------------
Nothing legitimate sends `Authorization` to a development API: the web app's
development modes send X-Dev-User-Email, and its proxy never forwards an
incoming Authorization header. What does send one is a web app running real
sign-in (AUTH_MODE=entra) in front of an API left in development mode — and
there, falling back to DEV_USER_EMAIL for a request with no dev header would
make every Microsoft account the same person. So such a request is refused, and
logged at error level: it is a deployment fault an operator has to find.
"""

import logging

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing_extensions import Annotated

from app.api.deps import DbSession
from app.api.errors import UnauthenticatedError
from app.core.config import settings
from app.models import User

logger = logging.getLogger(__name__)

#: Read off the model so the check cannot drift from the column it protects.
EMAIL_MAX_LENGTH: int = User.__table__.c.email.type.length

#: The unique index on users.email. PostgreSQL reports a unique-index violation
#: with the index's name as the constraint name.
_EMAIL_UNIQUE_INDEX = "ix_users_email"

_REFUSED_IN_PRODUCTION = (
    "Development identity is disabled in this environment, and no other sign-in exists yet."
)
_UNUSABLE_IDENTITY = "The development identity header is not a usable identity."
_BEARER_IN_DEVELOPMENT = (
    "This API is running the development identity and does not accept sign-in assertions."
)


def current_user(
    db: DbSession,
    x_dev_user_email: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the caller, creating the row on first sight.

    Just-in-time provisioning is what a real identity provider needs anyway, so
    this line survives the migration rather than being thrown away. The header
    override exists so tests can act as two different users in one process.
    """
    if settings.ENVIRONMENT == "production":
        # Error, not warning: this is a deployment serving people with an
        # identity scheme that believes whatever it is told.
        logger.error("dev_identity.refused", extra={"reason": "production_environment"})
        raise UnauthenticatedError(_REFUSED_IN_PRODUCTION)

    if authorization is not None:
        logger.error("dev_identity.refused", extra={"reason": "bearer_in_development_mode"})
        raise UnauthenticatedError(_BEARER_IN_DEVELOPMENT)

    email = _claimed_email(x_dev_user_email)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = _provision(db, email)
    return user


def _claimed_email(header: str | None) -> str:
    """The email this request claims, normalised, or a 401.

    Only an ABSENT header falls back to DEV_USER_EMAIL. A header that is present
    and says nothing is a malformed request, not a request to be the default
    user. A blank DEV_USER_EMAIL is refused by the same rule.
    """
    email = (settings.DEV_USER_EMAIL if header is None else header).strip().lower()

    # Checked after normalising: lower() can lengthen a string ("İ" -> "i̇").
    if not email:
        reason = "blank"
    elif len(email) > EMAIL_MAX_LENGTH:
        reason = "too_long"
    elif not email.isprintable():
        reason = "control_characters"
    else:
        return email

    # The reason, never the value: the value is somebody's email, or an
    # attacker's attempt at a log line.
    logger.warning("dev_identity.refused", extra={"reason": reason})
    raise UnauthenticatedError(_UNUSABLE_IDENTITY)


def _provision(db: Session, email: str) -> User:
    """Create the user, or return the one a concurrent first sight just created.

    Two requests seeing a new email at the same moment both miss the SELECT and
    both INSERT; the unique index lets exactly one win and hands the other an
    IntegrityError, which used to escape as a 500. The loser loses nothing: it
    wanted that row, and the row now exists.

    The INSERT runs in a SAVEPOINT so that losing rolls back only the insert,
    not the request's transaction. The re-read then sees the winner because the
    violation is only reported once the winner has COMMITTED (until then the
    INSERT waits on it), and READ COMMITTED — PostgreSQL's default and this
    application's — gives every statement a fresh snapshot.

    Only the email index's violation is absorbed. Any other integrity error is
    a real one and propagates.
    """
    try:
        with db.begin_nested():
            user = User(email=email)
            db.add(user)
            db.flush()
        return user
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != _EMAIL_UNIQUE_INDEX:
            raise
        winner = db.scalar(select(User).where(User.email == email))
        if winner is None:
            # The index said the row exists and the re-read cannot see it. Not
            # reachable under READ COMMITTED; if it ever is, the original error
            # is the honest thing to surface.
            raise
        logger.info("dev_identity.concurrent_first_sight")
        return winner


#: The development resolver as a dependency on its own. Routes use
#: `app.api.identity.CurrentUser`, which chooses between the modes.
DevelopmentUser = Annotated[User, Depends(current_user)]
