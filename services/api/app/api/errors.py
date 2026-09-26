"""Translating database and service errors into HTTP responses.

The mapping is keyed on the pair (SQLSTATE, diag.constraint_name), never on
message text. Both halves were verified against the running PostgreSQL 18.4:

  - An immutability trigger's RAISE ... USING ERRCODE = 'restrict_violation'
    arrives as SQLSTATE 23001 with constraint_name = None.
  - An ON DELETE RESTRICT violation is ALSO 23001 in PG 18 — but it carries a
    constraint name.

So SQLSTATE alone is not a discriminator: matching on 23001 alone would report
a blocked user deletion as "this version is confirmed". The absence of a
constraint name is the signal, and it is only safe to read that way because
this schema names every constraint and index explicitly.

Anything unrecognised is re-raised deliberately. A handler that turns every
database error into a tidy 4xx is a handler that hides schema bugs behind a
plausible-looking response.

What a re-raised error becomes is Starlette's own last resort: a 500 whose body
is the fixed text "Internal Server Error" — never the exception's message, its
SQL or its bound parameters (the app does not run with debug=True). The
traceback goes to the log. app/db/session.py's hide_parameters keeps
SQLAlchemy's own "[parameters: ...]" suffix out of it, but NOT PostgreSQL's
message: a constraint violation's DETAIL ("Failing row contains (...)",
"Key (email)=(...)") still quotes row values, so the log is not free of
personal data and must be treated as sensitive. `tests/test_security_errors.py`
holds both halves.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.services.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceError,
)

_TRIGGER_IMMUTABLE = "This profile version is confirmed and cannot be modified."

# (sqlstate, constraint_name) -> (status, message)
_CONSTRAINT_ERRORS: dict[tuple[str, str | None], tuple[int, str]] = {
    ("23001", None): (409, _TRIGGER_IMMUTABLE),
    ("23505", "uq_profiles_user_id"): (409, "A profile already exists for this user."),
    ("23505", "uq_profile_versions_profile_id_version_number"): (
        409,
        "That version number already exists for this profile.",
    ),
    ("23505", "uq_profile_versions_current"): (
        409,
        "Another version is already current for this profile.",
    ),
    ("23505", "uq_skills_profile_version_id_name_lower"): (
        422,
        "The same skill is listed twice in this snapshot.",
    ),
    ("23505", "project_technologies_pkey"): (
        422,
        "The same technology is listed twice on one project.",
    ),
}

# Every CHECK constraint in the profile domain is a client data error.
_CHECK_MESSAGES: dict[str, str] = {
    "ck_experiences_end_date_after_start_date": "An experience cannot end before it starts.",
    "ck_education_end_date_after_start_date": "An education entry cannot end before it starts.",
    "ck_projects_end_date_after_start_date": "A project cannot end before it starts.",
    "ck_certifications_expiration_after_issue": "A certification cannot expire before it is issued.",
    "ck_experiences_end_date_precision_paired": "An end date must be sent with its precision.",
    "ck_experiences_start_date_precision_paired": "A start date must be sent with its precision.",
    "ck_education_start_date_precision_paired": "A start date must be sent with its precision.",
    "ck_education_end_date_precision_paired": "An end date must be sent with its precision.",
    "ck_projects_start_date_precision_paired": "A start date must be sent with its precision.",
    "ck_projects_end_date_precision_paired": "An end date must be sent with its precision.",
}


class UnauthenticatedError(Exception):
    """The caller could not be identified at all. Answered as 401.

    Defined here, beside its HTTP mapping, rather than with the service errors:
    authentication is not a service-layer concept. Services take a user_id and
    never learn how it was established (see app/api/dev_identity.py), so only
    the identity seam raises this.

    The message is a fixed sentence chosen by the raiser. It never carries the
    presented credential, which is attacker-chosen text and, for the development
    identity, somebody's email address.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _problem(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, "code": code})


async def unauthenticated_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, UnauthenticatedError):
        raise exc
    return _problem(401, exc.message, "unauthenticated")


async def service_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, NotFoundError):
        return _problem(404, exc.message, "not_found")
    if isinstance(exc, ConflictError):
        return _problem(409, exc.message, "conflict")
    if isinstance(exc, ForbiddenError):
        return _problem(403, exc.message, "forbidden")
    raise exc


async def integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, IntegrityError):
        raise exc

    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)

    mapped = _CONSTRAINT_ERRORS.get((sqlstate, constraint))
    if mapped is not None:
        status, message = mapped
        return _problem(status, message, constraint or "version_immutable")

    if sqlstate == "23514" and constraint:
        message = _CHECK_MESSAGES.get(constraint, "The submitted data is invalid.")
        return _problem(422, message, constraint)

    if sqlstate == "23001" and constraint:
        return _problem(409, "That record is still referenced and cannot be removed.", constraint)

    # Unknown integrity error: let it become a 500 rather than a comfortable lie.
    raise exc


HANDLERS = (
    (ServiceError, service_error_handler),
    (IntegrityError, integrity_error_handler),
    (UnauthenticatedError, unauthenticated_error_handler),
)
