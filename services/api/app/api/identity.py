"""THE identity seam of the API: who a request is for.

Every route depends on `CurrentUser` and nothing else in the application knows
how identity is established. Services take `user_id: int` and enforce ownership
themselves; they do not know, and must not learn, which of these modes ran.

    AUTH_MODE=development  X-Dev-User-Email, resolved by app/api/dev_identity.py.
                           For a developer's machine and the test suites only:
                           it believes a header, so it refuses every request
                           under ENVIRONMENT=production.
    AUTH_MODE=entra        the web app's signed assertion of a Microsoft Entra
                           External ID sign-in, validated and mapped through
                           user_identities by app/api/assertion_identity.py.
                           X-Dev-User-Email is refused, not ignored.

A closed set (app/core/config.py). The mode is read per request from settings,
so there is no way to assemble the application with a route that skips it.
"""

import logging

from fastapi import Depends, Header
from typing_extensions import Annotated

from app.api import assertion_identity, dev_identity
from app.api.deps import DbSession
from app.core.config import settings
from app.models import User

logger = logging.getLogger(__name__)


def current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    x_dev_user_email: Annotated[str | None, Header()] = None,
) -> User:
    if settings.AUTH_MODE == "entra":
        return assertion_identity.asserted_user(db, authorization=authorization, x_dev_user_email=x_dev_user_email)
    return dev_identity.current_user(db, x_dev_user_email=x_dev_user_email, authorization=authorization)


CurrentUser = Annotated[User, Depends(current_user)]
