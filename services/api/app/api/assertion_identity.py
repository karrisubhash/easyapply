"""The production identity path: the web app's signed assertion of an Entra sign-in.

Under `AUTH_MODE=entra` the API believes exactly one thing about who is calling:
an `Authorization: Bearer <assertion>` the EasyApply web app minted for this
request after it had validated a Microsoft Entra External ID sign-in
(apps/web/src/server/auth/assertion.ts). Nothing else — no X-Dev-User-Email
(its presence is a refusal), no user id, no email, no claim a browser could
write. The web app is the OIDC relying party; the API trusts the web app,
through a secret only the two share, and re-checks the tenant for itself.

    HS256 JWT, 60 seconds:
      iss  "easyapply-web"            aud  "easyapply-api"
      sub  the Entra `oid`            idp  the Entra issuer the web app validated
      tid  the Entra tenant           jti  unique per assertion
      iat / nbf / exp                 email, name   display metadata only
      roles  ["EasyApply.Operator"] for an operator, else absent

Every way of being wrong is the same 401 with one fixed sentence; the category
goes to the log (never the token): missing, malformed, bad_algorithm,
bad_signature, expired, not_yet_valid, too_long_lived, wrong_issuer,
wrong_audience, wrong_idp, wrong_tenant, bad_subject, replayed, dev_header,
misconfigured.

The mapping from a validated identity to a user is (issuer, subject) through
`user_identities` — never email (app/models/user_identity.py). The issuer
column holds a CANONICAL key, `entra:<tenant GUID>`, not the issuer URL: the
tenant id is immutable and the URL is configuration (External ID's hostnames do
not even follow one pattern), so correcting ENTRA_ISSUER can never orphan every
account. The subject is the lower-cased `oid`. First sight provisions a user
and its mapping in one transaction; a concurrent first sight loses the unique
constraint and reads the winner.

Display metadata never decides authentication. A name with a zero-width joiner,
a non-breaking space or 256 characters is a valid Entra name; it is cleaned
(control characters removed, cut to the column) or dropped, logged, and the
request goes on. Only the identity claims can refuse a request.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import UnauthenticatedError
from app.core.config import identity_configuration_problems, settings
from app.models import User, UserIdentity

logger = logging.getLogger(__name__)

ASSERTION_ISSUER = "easyapply-web"
ASSERTION_AUDIENCE = "easyapply-api"
#: A real assertion is a few hundred bytes. Anything this long is not one.
MAX_TOKEN_LENGTH = 4096
#: The web app mints 60-second assertions; one claiming more was not minted by it.
MAX_LIFETIME_SECONDS = 300
#: Clock tolerance between the web and API hosts.
LEEWAY_SECONDS = 30
MIN_SECRET_LENGTH = 32

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_JTI = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_UNIQUE_IDENTITY = "uq_user_identities_issuer_subject"

_REFUSED = "The request did not carry a valid EasyApply sign-in."

EMAIL_MAX_LENGTH: int = UserIdentity.__table__.c.email.type.length
NAME_MAX_LENGTH: int = UserIdentity.__table__.c.display_name.type.length


class AssertionRefused(Exception):
    """Why an assertion was not accepted — a category for the log, never shown to the caller."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AssertedIdentity:
    #: The canonical provider key, `entra:<tenant GUID>` — see the module docstring.
    issuer: str
    subject: str
    email: str | None
    name: str | None
    roles: tuple[str, ...] = ()


def provider_key(tenant_id: str) -> str:
    return f"entra:{tenant_id.lower()}"


class _ReplayGuard:
    """Assertion ids seen recently, per process, until they expire.

    The web app mints one assertion per proxied request, so the same `jti`
    twice means a copy is being replayed. Per process only — several API
    workers each keep their own — which bounds, not removes, the window of a
    stolen assertion; its 60-second lifetime and audience are the main limits.

    Bounded, and FULL MEANS REFUSE: once `capacity` unexpired ids are held, a
    new assertion is refused (`replay_guard_full`, logged at error level)
    rather than an unexpired id being forgotten — forgetting one would let
    exactly that assertion be replayed. Only valid assertions reach this, so
    only the web app can fill it: at the default 200,000 that is more than
    2,000 requests a second to one process, sustained for 90 seconds.
    """

    def __init__(self, capacity: int = 200_000) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def first_use(self, jti: str, expires_at: float, now: float) -> bool:
        with self._lock:
            while self._seen and next(iter(self._seen.values())) < now:
                self._seen.popitem(last=False)
            if jti in self._seen:
                return False
            if len(self._seen) >= self._capacity:
                logger.error("identity.replay_guard_full", extra={"capacity": self._capacity})
                raise AssertionRefused("replay_guard_full")
            self._seen[jti] = expires_at
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


replay_guard = _ReplayGuard()


def _secrets() -> list[str]:
    # Checked per request as well as at startup: settings changed after start
    # (or an entry point that skipped the lifespan) must refuse, not guess.
    if identity_configuration_problems(settings):
        logger.error("identity.misconfigured", extra={"reason": "configuration"})
        raise AssertionRefused("misconfigured")
    current = settings.IDENTITY_ASSERTION_SECRET
    if len(current) < MIN_SECRET_LENGTH:
        logger.error("identity.misconfigured", extra={"reason": "assertion_secret"})
        raise AssertionRefused("misconfigured")
    previous = settings.IDENTITY_ASSERTION_SECRET_PREVIOUS
    return [current, previous] if len(previous) >= MIN_SECRET_LENGTH else [current]


def _metadata(claims: dict, key: str, limit: int) -> str | None:
    """A display value, cleaned — never a reason to refuse the request."""
    value = claims.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        logger.info("identity.metadata_dropped", extra={"claim": key, "reason": "not_text"})
        return None
    # Control characters go; a lone surrogate (not text at all — no column
    # could store it) becomes U+FFFD, as the web app's cleaning does.
    cleaned = "".join(
        "\ufffd" if unicodedata.category(char) == "Cs" else char
        for char in unicodedata.normalize("NFC", value)
        if unicodedata.category(char) != "Cc"
    ).strip()
    if len(cleaned) > limit:
        logger.info("identity.metadata_truncated", extra={"claim": key})
        cleaned = cleaned[:limit].strip()
    return cleaned or None


def _roles(claims: dict) -> tuple[str, ...]:
    value = claims.get("roles")
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 50 or not all(isinstance(role, str) and 0 < len(role) <= 120 for role in value):
        logger.info("identity.metadata_dropped", extra={"claim": "roles", "reason": "shape"})
        return ()
    return tuple(value)


def validate_assertion(
    authorization: str | None,
    *,
    x_dev_user_email: str | None = None,
    now: float | None = None,
) -> AssertedIdentity:
    """The identity an assertion proves, or `AssertionRefused` with the reason."""
    now = time.time() if now is None else now

    # The web app never sends the development header in this mode. A request
    # carrying one did not come from it the way it should have.
    if x_dev_user_email is not None:
        raise AssertionRefused("dev_header")
    secrets = _secrets()

    if authorization is None:
        raise AssertionRefused("missing")
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token or " " in token or len(token) > MAX_TOKEN_LENGTH:
        raise AssertionRefused("malformed")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise AssertionRefused("malformed") from None
    # Checked before any key is used: `none`, RS256-with-a-shared-secret and
    # every other algorithm substitution die here, not inside a library default.
    if header.get("alg") != "HS256":
        raise AssertionRefused("bad_algorithm")

    claims: dict | None = None
    for secret in secrets:
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=ASSERTION_AUDIENCE,
                issuer=ASSERTION_ISSUER,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud", "jti"]},
            )
            break
        except jwt.InvalidSignatureError:
            continue  # maybe signed with the previous secret, during a rotation
        except jwt.ExpiredSignatureError:
            raise AssertionRefused("expired") from None
        except jwt.ImmatureSignatureError:
            raise AssertionRefused("not_yet_valid") from None
        except jwt.InvalidAudienceError:
            raise AssertionRefused("wrong_audience") from None
        except jwt.InvalidIssuerError:
            raise AssertionRefused("wrong_issuer") from None
        except jwt.InvalidTokenError:
            raise AssertionRefused("malformed") from None
    if claims is None:
        raise AssertionRefused("bad_signature")

    issued, expires = claims["iat"], claims["exp"]
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise AssertionRefused("malformed")
    if issued > now + LEEWAY_SECONDS:
        raise AssertionRefused("not_yet_valid")
    if expires - issued > MAX_LIFETIME_SECONDS:
        raise AssertionRefused("too_long_lived")

    # The web app validated the Entra token; the API still insists it was THIS
    # tenant's, so a web app pointed at another tenant cannot provision users here.
    if claims.get("idp") != settings.ENTRA_ISSUER:
        raise AssertionRefused("wrong_idp")
    tenant = claims.get("tid")
    if not isinstance(tenant, str) or tenant.lower() != settings.ENTRA_TENANT_ID.lower():
        raise AssertionRefused("wrong_tenant")
    subject = claims["sub"]
    if not isinstance(subject, str) or not GUID.fullmatch(subject):
        raise AssertionRefused("bad_subject")
    jti = claims["jti"]
    if not isinstance(jti, str) or not _JTI.fullmatch(jti):
        raise AssertionRefused("malformed")

    email = _metadata(claims, "email", EMAIL_MAX_LENGTH)
    name = _metadata(claims, "name", NAME_MAX_LENGTH)
    roles = _roles(claims)

    if not replay_guard.first_use(jti, expires + LEEWAY_SECONDS, now):
        raise AssertionRefused("replayed")

    return AssertedIdentity(
        issuer=provider_key(tenant), subject=subject.lower(), email=email, name=name, roles=roles
    )


def resolve_user(db: Session, identity: AssertedIdentity) -> User:
    """The user this identity maps to, provisioning on first sight.

    Committed here, before the handler runs, so a first sign-in is durable
    whatever the request goes on to do (a GET commits nothing of its own). The
    request's session has no other work pending at this point.
    """
    row = db.execute(
        select(User, UserIdentity)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.issuer == identity.issuer, UserIdentity.subject == identity.subject)
    ).first()
    if row is None:
        return _provision(db, identity)

    user, mapping = row
    if mapping.email != identity.email or mapping.display_name != identity.name:
        # Metadata only: the account is the same whatever the provider now says.
        mapping.email = identity.email
        mapping.display_name = identity.name
        db.commit()
        logger.info("identity.metadata_updated", extra={"user_id": user.id})
    return user


def _provision(db: Session, identity: AssertedIdentity) -> User:
    """A new user and its mapping, or the ones a concurrent first sight just made.

    Never linked to an existing user by email: a new (issuer, subject) is a new
    account, whoever else claims the same address.
    """
    try:
        with db.begin_nested():
            user = User(email=None)
            db.add(user)
            db.flush()
            db.add(
                UserIdentity(
                    user_id=user.id,
                    issuer=identity.issuer,
                    subject=identity.subject,
                    email=identity.email,
                    display_name=identity.name,
                )
            )
            db.flush()
        db.commit()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != _UNIQUE_IDENTITY:
            raise
        winner = db.scalar(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(UserIdentity.issuer == identity.issuer, UserIdentity.subject == identity.subject)
        )
        if winner is None:
            raise
        logger.info("identity.concurrent_first_sight", extra={"user_id": winner.id})
        return winner
    logger.info("identity.provisioned", extra={"user_id": user.id})
    return user


def asserted_user(db: Session, *, authorization: str | None, x_dev_user_email: str | None) -> User:
    try:
        identity = validate_assertion(authorization, x_dev_user_email=x_dev_user_email)
    except AssertionRefused as refused:
        logger.warning("identity.refused", extra={"reason": refused.reason})
        raise UnauthenticatedError(_REFUSED) from None
    return resolve_user(db, identity)
