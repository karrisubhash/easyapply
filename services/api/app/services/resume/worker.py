"""Executing one processing run, safely.

The worker is an execution mechanism, not a second resume domain. It knows how
to take a run, keep hold of it, hand it to `pipeline.process_run`, and let go —
and nothing at all about PDFs, OCR, candidates or profile versions. Every line
of business logic it appears to have is a call into something that already
existed.

The order is load-bearing:

    acquire lease → heartbeat → pipeline → release lease → maybe retry

Acquire first, because a run nobody owns may be picked up by anyone. Release
last, because a lease held past the work is a run nobody can recover until it
expires. And the retry decision comes after the release, so the run it opens is
immediately claimable by whichever worker gets there first — including a
different one.

Sessions are the other thing the worker owns. `process_run` commits, so the
worker cannot share the request's session; it takes a session scope and the
caller decides what that means. In production it is a new SessionLocal per run.
In tests it is the test's own session, left open.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import ResumeProcessingRun
from app.services.resume import lease, pipeline, retry

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


@dataclass(frozen=True)
class RunOutcome:
    """What the worker did with a run, for callers and for tests.

    `acquired` false is not an error: it is the normal answer when another
    worker got there first, which is exactly the condition this design exists
    to make ordinary.
    """

    run_id: int
    acquired: bool
    status: str | None = None
    error_code: str | None = None
    retry_run_id: int | None = None


class Heartbeat:
    """Renews a lease in the background for as long as the work runs.

    Its own thread and its own session, because the pipeline is using the
    worker's session and a SQLAlchemy Session is not safe to share between
    threads. `stop()` is idempotent and always called from a finally block —
    a heartbeat outliving its work would keep a dead run leased.
    """

    def __init__(
        self,
        *,
        run_id: int,
        owner: str,
        interval_seconds: float,
        lease_seconds: int,
        session_scope: SessionScope,
    ) -> None:
        self._run_id = run_id
        self._owner = owner
        self._interval = interval_seconds
        self._lease_seconds = lease_seconds
        self._session_scope = session_scope
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Set when a renewal reports we no longer own the run — i.e. we were
        #: reaped while working. Readable by the worker after the fact.
        self.lost = threading.Event()

    def start(self) -> None:
        if self._interval <= 0:
            return
        self._thread = threading.Thread(
            target=self._beat, name=f"resume-heartbeat-{self._run_id}", daemon=True
        )
        self._thread.start()

    def _beat(self) -> None:
        # wait() rather than sleep(): stop() then interrupts the pause instead
        # of being noticed one interval later.
        while not self._stop.wait(self._interval):
            try:
                with self._session_scope() as db:
                    if not lease.renew(
                        db,
                        run_id=self._run_id,
                        owner=self._owner,
                        lease_seconds=self._lease_seconds,
                    ):
                        self.lost.set()
                        return
            except Exception:
                # A failed renewal is not fatal on its own — the lease still has
                # time on it, and the next beat may succeed. Letting the thread
                # die silently is what would be fatal.
                logger.exception(
                    "resume heartbeat failed for run %s", self._run_id
                )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class ResumeWorker:
    """Runs queued resume work. One instance per process."""

    def __init__(
        self,
        *,
        session_scope: SessionScope | None = None,
        queue=None,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: float | None = None,
        storage=None,
        structurer=None,
        ocr_engine=None,
    ) -> None:
        self.worker_id = worker_id or lease.new_worker_id()
        self._session_scope = session_scope or default_session_scope
        self._lease_seconds = (
            lease_seconds if lease_seconds is not None else settings.RESUME_LEASE_SECONDS
        )
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else settings.RESUME_LEASE_HEARTBEAT_SECONDS
        )
        # Adapters are held rather than resolved per run so a test can inject
        # them once. Production passes none and the pipeline resolves its own.
        self._storage = storage
        self._structurer = structurer
        self._ocr_engine = ocr_engine
        #: Set after construction by the queue that owns this worker, so a retry
        #: opened here goes back onto the same queue. A cycle broken with a
        #: setter rather than with a second queue abstraction.
        self.queue = queue

    # --- one run --------------------------------------------------------------

    def execute(self, run_id: int) -> RunOutcome:
        """Claim a run and take it to a terminal state.

        Never raises for the ordinary failures: a run that cannot be claimed, or
        one whose pipeline fails, is a reported outcome. The worker is called
        from a thread pool where an exception is only a log line, so anything
        worth knowing has to be in the return value or in the database.
        """
        with self._session_scope() as db:
            if not lease.acquire(
                db,
                run_id=run_id,
                owner=self.worker_id,
                lease_seconds=self._lease_seconds,
            ):
                return RunOutcome(run_id=run_id, acquired=False)

            heartbeat = Heartbeat(
                run_id=run_id,
                owner=self.worker_id,
                interval_seconds=self._heartbeat_seconds,
                lease_seconds=self._lease_seconds,
                session_scope=self._session_scope,
            )
            heartbeat.start()
            try:
                run = pipeline.process_run(
                    db,
                    run_id=run_id,
                    storage=self._storage,
                    structurer=self._structurer,
                    ocr_engine=self._ocr_engine,
                )
            finally:
                heartbeat.stop()
                # Released even if the pipeline raised. An unreleased lease is
                # recoverable — it expires — but only after a delay nobody
                # benefits from.
                lease.release(db, run_id=run_id, owner=self.worker_id)

            if heartbeat.lost.is_set():
                # The reaper took this run from us mid-flight and has already
                # accounted for it. Whatever the pipeline just wrote stands; we
                # simply do not get to decide about a retry as well.
                logger.warning(
                    "resume.run.lease_lost",
                    extra={"resume_run_id": run_id, "lease_owner": self.worker_id},
                )
                return RunOutcome(run_id=run_id, acquired=True, status=run.status)

            retry_run = None
            if run.status == "failed":
                retry_run = retry.schedule_retry(
                    db, failed_run=run, queue=self.queue
                )

            return RunOutcome(
                run_id=run_id,
                acquired=True,
                status=run.status,
                error_code=run.error_code,
                retry_run_id=retry_run.id if retry_run else None,
            )

    # --- polling --------------------------------------------------------------

    def poll_once(self, *, limit: int | None = None) -> list[RunOutcome]:
        """Take whatever is claimable right now.

        This is how a standalone worker process finds work without a message
        broker: the run table already knows what is outstanding. Reading the
        candidates and claiming them are separate steps on purpose — the read is
        advisory, and `acquire` re-checks against the live row.
        """
        batch = limit if limit is not None else settings.RESUME_WORKER_BATCH
        with self._session_scope() as db:
            run_ids = lease.claimable_run_ids(db, limit=batch)

        return [self.execute(run_id) for run_id in run_ids]


def get_worker(**kwargs) -> ResumeWorker:
    return ResumeWorker(**kwargs)


def latest_run_status(db: Session, run_id: int) -> str | None:
    """Small read used by tests and by the worker CLI's logging."""
    run = db.get(ResumeProcessingRun, run_id)
    return run.status if run else None
