"""The operator authorization boundary, deliberately explicit.

One named seam, the way `app/api/identity.py` is one named seam for user
identity. Under real sign-in (AUTH_MODE=entra) it requires BOTH the shared
token AND a valid identity assertion for a person holding the Entra operator
app role (ENTRA_OPERATOR_ROLE) — see "UNDER REAL SIGN-IN" below. In the
development mode it is the shared token alone, and that is NOT
production-secure; the sections below say exactly what it does and does not do.

WHAT IT ACTUALLY ENFORCES TODAY
-------------------------------
A shared secret in a header, compared in constant time:

    OPERATOR_API_ENABLED  false  -> every operator route answers 404.
                                    Not 403: a disabled admin surface should not
                                    confirm it exists.
    OPERATOR_API_TOKEN    set    -> X-Operator-Token must match it, or 403.
                          unset  -> in ENVIRONMENT=development the boundary
                                    is OPEN and every request logs a warning;
                                    anywhere else it is CLOSED, every request a
                                    403 with the missing setting in the log.

Unset used to mean open everywhere. That made the safe-looking default the
dangerous one: a deployment that simply forgot a setting served a cross-user
surface to anybody who could reach the process. It now fails closed outside a
developer's own machine, which turns the same omission into an operator screen
that does not work — noticed and fixed — rather than one that works for anyone.

UNDER REAL SIGN-IN (AUTH_MODE=entra)
------------------------------------
The token is necessary, not sufficient. The request must also carry the web
app's assertion (app/api/assertion_identity.py) naming a person whose ID token
held the operator app role; anything else is the same 403. The web app only
forwards /internal/* for such a person, answers everyone else 404, and attaches
the token only after that check. Revoking the role takes effect at the person's
next sign-in — at most the web session's lifetime (SESSION_MAX_AGE_SECONDS) —
and ending every session at once is rotating the web app's SESSION_SECRET.

WHAT IT DOES NOT ENFORCE (in the development mode)
--------------------------------------------------
* There are no operator accounts. A token is a password shared by everyone who
  has it, with no identity, no revocation and no audit trail of who acted.
* The token does not distinguish people, so "who looked at this?" is
  unanswerable. Every access is logged, but only as "someone with the token".
* Outside development, configuring a token is the only way to use the operator
  API at all: set OPERATOR_API_TOKEN to the same value in services/api/.env and
  apps/web/.env.local.
* The Next.js proxy attaches the token server-side for `/api/backend/internal/*`
  only. That keeps it out of browser JavaScript, but it does NOT keep it out of
  reach: anyone who can load the web app can call that path and the proxy will
  attach the token for them. The web app is not a second authorization boundary
  and must not be treated as one.

Because of that last point the operator endpoints expose ids, timestamps, counts
and error codes and nothing else — no filenames, no page text, no structured
payloads, no email addresses. The data minimisation is doing the work that the
authorization cannot do yet, and it should stay that way even after real
authorization lands.

WHAT IS STILL MISSING
---------------------
The role check above is in place; the token is kept as a second factor for the
operator surface (a web server that forwarded /internal/* by mistake still
cannot use it without the token). Each granted access logs `operator.access`
with a pseudonymous operator key — a truncated SHA-256 of the account key, not
the raw Entra claim — so "which operator looked?" is answerable from the log
joined with user_identities, and the log alone names nobody. Nothing else in
the application knows this file exists.
"""

import hashlib
import logging
import secrets

from fastapi import Depends, Header
from typing_extensions import Annotated

from app.core.config import settings
from app.services.errors import ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)

#: One sentence for every refusal. "No token configured" and "wrong token" look
#: the same from outside; the log line carries which it was.
_REFUSED = "A valid operator token is required."


class Operator:
    """Whoever is behind the operator boundary.

    Carries no identity because there is none to carry: the token says someone
    is allowed, never who. A real implementation returns a principal here, which
    is the point of returning an object at all rather than None.
    """

    __slots__ = ("authenticated",)

    def __init__(self, *, authenticated: bool) -> None:
        #: False only in development with no token configured, where the
        #: boundary stands open. Surfaced so a route, a log or a banner can say
        #: so.
        self.authenticated = authenticated

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<Operator authenticated={self.authenticated}>"


def require_operator(
    x_operator_token: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Operator:
    """Allow an operator request, or refuse it.

    Raises rather than returning a verdict so that forgetting to check the
    result is not a way to bypass it.

    Under real sign-in (AUTH_MODE=entra) the token alone is not enough: the
    request must also carry a valid identity assertion for a person holding the
    Entra operator app role. The web app only forwards /internal/* for such a
    person, and attaches the token only then; this check makes the API refuse
    the cross-user operator surface to anyone else even if something in front
    of it did not.
    """
    if not settings.OPERATOR_API_ENABLED:
        # 404 rather than 403. A disabled operator surface should be
        # indistinguishable from one that was never built.
        raise NotFoundError("Not found.")

    expected = settings.OPERATOR_API_TOKEN
    if settings.AUTH_MODE == "entra":
        # Real sign-in has no "open in development" posture: the token AND the
        # role, whatever ENVIRONMENT says. Checked before the development-mode
        # branches below so that no combination of settings reaches them.
        return _entra_operator(x_operator_token, authorization, expected)

    if authorization is not None:
        # A bearer assertion at a development-mode API is a web app in entra
        # mode in front of an API left in development — the mode mismatch
        # app/api/dev_identity.py refuses for users. Refused here too, so the
        # operator surface cannot be the exception.
        logger.error("operator.access_denied", extra={"boundary": "operator", "reason": "bearer_in_development_mode"})
        raise ForbiddenError(_REFUSED)

    if not expected and settings.ENVIRONMENT == "development":
        # A developer's own machine, where the rest of the application
        # authenticates nobody either and both servers bind loopback: open, and
        # loud about it, exactly as before ENVIRONMENT existed. Anywhere else an
        # unset token fails closed (below) — the omission that used to open a
        # cross-user surface in a deployment now produces a 403 instead.
        logger.warning(
            "operator.unauthenticated_access",
            extra={
                "boundary": "operator",
                "reason": "no OPERATOR_API_TOKEN configured (development)",
            },
        )
        return Operator(authenticated=False)
    if not expected:
        # Fail closed. No token configured is a deployment that has not decided
        # who may read across every user, and the only safe reading of "not
        # decided" is "nobody". Logged per request, with the reason, so the
        # developer staring at a 403 finds out what to set; the response itself
        # says only what any refusal says.
        logger.warning(
            "operator.access_denied",
            extra={
                "boundary": "operator",
                "reason": "no OPERATOR_API_TOKEN configured",
                "presented": bool(x_operator_token),
            },
        )
        raise ForbiddenError(_REFUSED)

    if not x_operator_token or not _tokens_match(x_operator_token, expected):
        logger.warning(
            "operator.access_denied",
            extra={"boundary": "operator", "presented": bool(x_operator_token)},
        )
        raise ForbiddenError(_REFUSED)

    return Operator(authenticated=True)


def _entra_operator(x_operator_token: str | None, authorization: str | None, expected: str | None) -> Operator:
    """AUTH_MODE=entra: the shared token and an assertion carrying the operator role, or 403."""
    if not expected:
        logger.warning(
            "operator.access_denied",
            extra={"boundary": "operator", "reason": "no OPERATOR_API_TOKEN configured", "presented": bool(x_operator_token)},
        )
        raise ForbiddenError(_REFUSED)
    if not x_operator_token or not _tokens_match(x_operator_token, expected):
        logger.warning(
            "operator.access_denied",
            extra={"boundary": "operator", "presented": bool(x_operator_token)},
        )
        raise ForbiddenError(_REFUSED)

    # Imported here: the operator boundary must not import the user seam at
    # module load (the user seam imports the models and the database).
    from app.api.assertion_identity import AssertionRefused, validate_assertion

    try:
        identity = validate_assertion(authorization)
    except AssertionRefused as refused:
        logger.warning("operator.access_denied", extra={"boundary": "operator", "reason": refused.reason})
        raise ForbiddenError(_REFUSED) from None
    if settings.ENTRA_OPERATOR_ROLE not in identity.roles:
        logger.warning("operator.access_denied", extra={"boundary": "operator", "reason": "role_missing"})
        raise ForbiddenError(_REFUSED)
    operator_key = hashlib.sha256(f"{identity.issuer}:{identity.subject}".encode()).hexdigest()[:12]
    logger.info("operator.access", extra={"boundary": "operator", "operator": operator_key})
    return Operator(authenticated=True)


def _tokens_match(presented: str, expected: str) -> bool:
    """Compare a presented token with the configured one, in constant time.

    Constant time because a token compared with == leaks its prefix to anyone
    willing to time the comparison.

    As BYTES, because `secrets.compare_digest` refuses a `str` holding anything
    outside ASCII with a TypeError — and a header is attacker-chosen, so one
    non-ASCII character in X-Operator-Token used to be a 500 instead of a 403.
    UTF-8 on both sides: it represents anything either side can hold, and equal
    bytes means equal strings, so encoding cannot manufacture a match.

    Configure an ASCII token. Clients disagree about which bytes a non-ASCII
    header value becomes (fetch sends latin-1, curl sends UTF-8, and Starlette
    decodes whatever arrives as latin-1), so a non-ASCII token may never match
    from some client — refused, never crashed and never wrongly admitted.
    """
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


CurrentOperator = Annotated[Operator, Depends(require_operator)]
