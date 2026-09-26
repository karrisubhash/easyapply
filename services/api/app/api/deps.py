"""Request-scoped dependencies.

This is the get_db that app/db/session.py deliberately does not define: a
session's lifetime is an HTTP concern, not a database-engine concern.

It never commits. FastAPI runs the teardown of a yield-dependency *after* the
response has been written, so a commit here would fail silently with 200
already on the wire. Committing belongs to the service layer, inside the
handler, where an error can still become a response.
"""

from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from typing_extensions import Annotated

from app.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        # A no-op after a successful service commit; the safety net when a
        # handler raised before reaching one.
        db.rollback()
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_processing_queue(request: Request):
    """The application's processing queue.

    Read off app.state rather than constructed here: the queue owns a thread
    pool and a worker, so there is exactly one per process and its lifetime is
    the application's. A dependency rather than a module-level singleton so
    tests can substitute one that records instead of running.
    """
    return request.app.state.processing_queue


ProcessingQueueDep = Annotated[object, Depends(get_processing_queue)]


def get_tailoring_queue(request: Request):
    """The application's tailoring queue.

    A second queue rather than a shared one, for the reason
    `app/services/tailoring/queue.py` gives: the two carry different work and a
    stuck resume must not starve a tailoring request. Read off app.state for the
    same reason the resume queue is — one pool per process, whose lifetime is
    the application's.
    """
    return request.app.state.tailoring_queue


TailoringQueueDep = Annotated[object, Depends(get_tailoring_queue)]
