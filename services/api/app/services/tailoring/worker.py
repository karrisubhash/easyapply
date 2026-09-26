"""Executing one tailoring run, off the request thread.

The mirror of `app/services/resume/worker.py`, sized down. There is no lease
here and no heartbeat: this worker opens a session, calls the pipeline, and lets
go. See `TAILORING_STALE_AFTER_SECONDS` in `app/core/config.py` for why a run
that dies mid-flight is handled by the read model and a retry the user asks for
rather than by a reaper.

Sessions are the one thing it owns, and it owns them the same way the resume
worker does: `process_run` commits, so the worker cannot share the request's
session. It takes a session scope and the caller decides what that means — a
new SessionLocal per run in production, the test's own session left open under
pytest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.tailoring import pipeline

logger = logging.getLogger(__name__)

SessionScope = Callable[[], "AbstractContextManager[Session]"]  # noqa: F821


@contextmanager
def default_session_scope() -> Iterator[Session]:
    """A session per unit of work, closed when the work ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TailoringWorker:
    """Turns a run id into a finished run."""

    #: Set by whichever queue owns this worker, so a pipeline that wanted to
    #: enqueue follow-on work could. Nothing does yet; the attribute exists so
    #: the queues can be constructed exactly as the resume ones are.
    queue: object | None = None

    def __init__(self, session_scope: SessionScope | None = None) -> None:
        self._session_scope = session_scope or default_session_scope

    def execute(self, run_id: int):
        with self._session_scope() as db:
            try:
                return pipeline.process_run(db, run_id=run_id)
            except Exception:
                # The pipeline converts every failure it models into a terminal
                # run. Reaching here means something outside it raised — a
                # connection lost on the way in, most likely. Logged rather
                # than swallowed into a Future nobody reads.
                logger.exception("tailoring worker failed for run %s", run_id)
                db.rollback()
                return None
