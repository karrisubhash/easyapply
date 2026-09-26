"""Service-layer errors.

Deliberately not HTTPException: the service must stay callable from something
that is not a web request. The HTTP mapping lives in app/api/errors.py.
"""


class ServiceError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """The addressed resource does not exist for this caller."""


class ConflictError(ServiceError):
    """The request is refused by the state of the resource."""


class ForbiddenError(ServiceError):
    """The caller is known and is not allowed to do this.

    Distinct from NotFoundError, which is what an endpoint answers when it does
    not wish to confirm that a thing exists at all.
    """
