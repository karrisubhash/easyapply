"""Recovering runs whose worker went away.

Scope is deliberately one question: which runs have stopped making progress, and
what does the state machine say should happen to them? It is not a scheduler, it
owns no cron vocabulary, and it will not grow other jobs — when a real scheduler
arrives, this becomes something it calls.

"Stopped making progress" has three shapes, recovered differently because they
mean different things:

  * ABANDONED — a lease exists and has expired. A worker claimed this run and
    then died: mid-deploy, OOM-killed, host lost. Partial work may have been
    written, and the run is sitting in whatever state it reached. It is failed
    with `worker_abandoned` at the stage its status names, and the retry policy
    then decides whether a fresh attempt opens beside it.

  * ORPHANED — no lease was ever taken, no retry time is set, and the run has
    been sitting past the grace period. The enqueue was lost: an API process
    committed the run and restarted before its pool reached it. Nothing was
    attempted, so there is nothing to fail and no attempt to burn. It is simply
    put back on the queue.

  * DUE — a scheduled retry whose backoff has elapsed. It was created
    un-enqueued on purpose, so something has to notice when its time arrives;
    doing that by query rather than by timer is what lets a scheduled retry
    survive a restart and stops any one process from being able to lose it.
    Nothing about the run changes — it is offered to the queue exactly as it is.

The first two distinctions are why an attempt is not silently consumed by an
infrastructure restart, and why a worker that genuinely died does not lose its
audit trail. The third is what makes a persisted backoff into a real delay.

An expired lease never causes an early retry. Recovery fails the abandoned run
and asks the retry policy for a successor, and that successor waits like any
other: `worker_abandoned` is a transient code, not a shortcut past the backoff.

Recoverable states: every non-terminal status. `completed` and `failed` runs are
never touched — they are the record, and a reaper that could rewrite them would
be a reaper that could rewrite history.

Recovery never reopens a run. An abandoned run stays failed forever and a new
attempt starts beside it, so "attempt 1 abandoned, attempt 2 completed" is what
the table says.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.resume import lease, retry
from app.services.resume.pipeline import STAGE_FOR_STATUS, ProcessingFailed, fail_run

logger = logging.getLogger(__name__)

#: Written to error_code when a lease expires under a live run. Classified as
#: transient in failures.py: the document was never the problem.
ABANDONED_ERROR_CODE = "worker_abandoned"

ABANDONED = "abandoned"
ORPHANED = "orphaned"
DUE = "due"


@dataclass(frozen=True)
class Recovery:
    """One run the reaper acted on."""

    run_id: int
    document_id: int
    kind: str
    attempt: int
    retry_run_id: int | None = None


def recover_abandoned_runs(
    db: Session,
    *,
    queue=None,
    now: datetime | None = None,
    limit: int = 50,
) -> list[Recovery]:
    """One sweep. Returns what it did, so a caller can log or assert on it."""
    moment = now or datetime.now(timezone.utc)
    recoveries: list[Recovery] = []

    recoveries.extend(_recover_expired(db, queue=queue, now=moment, limit=limit))
    recoveries.extend(_requeue_orphans(db, queue=queue, now=moment, limit=limit))
    # Last, so a retry opened by the expired-lease pass above is offered in the
    # same sweep when its backoff is zero — and simply waits for the next one
    # when it is not.
    recoveries.extend(_release_due_retries(db, queue=queue, now=moment, limit=limit))
    return recoveries


def _recover_expired(
    db: Session, *, queue, now: datetime, limit: int
) -> list[Recovery]:
    recoveries: list[Recovery] = []

    owner = f"reaper/{lease.new_worker_id()}"

    for run in lease.expired_runs(db, limit=limit, now=now):
        # Take the run before writing to it. Without this, the reaper and a
        # worker that is merely slow to renew could both be acting on the same
        # row; with it, whoever acquires is the only one entitled to finish it,
        # and the loser's next heartbeat tells it so.
        if not lease.acquire(
            db,
            run_id=run.id,
            owner=owner,
            lease_seconds=settings.RESUME_LEASE_SECONDS,
            now=now,
        ):
            continue

        db.refresh(run)
        if run.is_terminal:
            # It finished between the query and the claim.
            lease.release(db, run_id=run.id, owner=owner)
            continue

        stage = STAGE_FOR_STATUS.get(run.status, "validate")
        document_id = run.resume_document_id
        attempt = run.attempt

        fail_run(
            db,
            run,
            ProcessingFailed(
                stage,
                ABANDONED_ERROR_CODE,
                "Processing stopped because the worker became unavailable.",
            ),
        )
        db.commit()
        lease.release(db, run_id=run.id, owner=owner)

        retry_run = retry.schedule_retry(db, failed_run=run, queue=queue)
        logger.warning(
            "resume.run.recovered",
            extra={
                "resume_run_id": run.id,
                "resume_document_id": document_id,
                "attempt": attempt,
                "kind": ABANDONED,
                "stage": stage,
                "retry_run_id": retry_run.id if retry_run else None,
            },
        )
        recoveries.append(
            Recovery(
                run_id=run.id,
                document_id=document_id,
                kind=ABANDONED,
                attempt=attempt,
                retry_run_id=retry_run.id if retry_run else None,
            )
        )

    return recoveries


def _requeue_orphans(
    db: Session, *, queue, now: datetime, limit: int
) -> list[Recovery]:
    """Runs nobody ever claimed: put them back on the queue, unchanged."""
    cutoff = now - timedelta(seconds=settings.RESUME_REAPER_UNCLAIMED_GRACE_SECONDS)
    recoveries: list[Recovery] = []

    for run in lease.unclaimed_runs(db, limit=limit, older_than=cutoff):
        logger.warning(
            "resume.run.recovered",
            extra={
                "resume_run_id": run.id,
                "resume_document_id": run.resume_document_id,
                "attempt": run.attempt,
                "kind": ORPHANED,
            },
        )
        if queue is not None:
            queue.enqueue(run.id)
        recoveries.append(
            Recovery(
                run_id=run.id,
                document_id=run.resume_document_id,
                kind=ORPHANED,
                attempt=run.attempt,
            )
        )

    return recoveries


def _release_due_retries(
    db: Session, *, queue, now: datetime, limit: int
) -> list[Recovery]:
    """Scheduled retries whose wait is over: offer them, unchanged.

    Nothing is written. The run already says everything — which attempt it is,
    when it became eligible — so this only tells the queue it exists. That is
    what keeps a backoff from needing a scheduler: the delay is a column, and
    noticing it has passed is a SELECT anyone can run.

    Re-offering a run that is still queued is harmless and expected. The queue
    deduplicates what it is already holding, and the lease arbitrates whoever
    actually arrives — so a sweep every interval converges without needing to
    track what it has already offered.
    """
    recoveries: list[Recovery] = []

    for run in lease.due_retry_runs(db, limit=limit, now=now):
        logger.info(
            "resume.retry.eligible",
            extra={
                "resume_run_id": run.id,
                "resume_document_id": run.resume_document_id,
                "attempt": run.attempt,
                "next_retry_at": run.next_retry_at.isoformat()
                if run.next_retry_at
                else None,
                # Not a failure code of its own: the code that caused the wait.
                "failure_code": None,
                "kind": DUE,
            },
        )
        if queue is not None:
            queue.enqueue(run.id)
        recoveries.append(
            Recovery(
                run_id=run.id,
                document_id=run.resume_document_id,
                kind=DUE,
                attempt=run.attempt,
            )
        )

    return recoveries


class ReaperLoop:
    """Runs the sweep on a timer, in this process.

    A timer and a stop flag, nothing more. It is not a scheduler and must not
    become one: a second job added here would be a job with no queue, no
    retries and no visibility, which is how an accidental cron daemon starts.

    Safe to run in several processes at once. Every recovery goes through the
    same atomic lease acquisition a worker uses, so two reapers racing over one
    abandoned run end with exactly one of them recovering it.
    """

    def __init__(
        self,
        *,
        queue=None,
        interval_seconds: float | None = None,
        enabled: bool | None = None,
        session_scope=None,
    ) -> None:
        from app.services.resume.worker import default_session_scope

        self._queue = queue
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else settings.RESUME_REAPER_INTERVAL_SECONDS
        )
        self._enabled = (
            enabled if enabled is not None else settings.RESUME_REAPER_ENABLED
        )
        self._session_scope = session_scope or default_session_scope
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled or self._interval <= 0:
            logger.info("resume.reaper.disabled")
            return
        self._thread = threading.Thread(
            target=self._loop, name="resume-reaper", daemon=True
        )
        self._thread.start()
        logger.info(
            "resume.reaper.started", extra={"interval_seconds": self._interval}
        )

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.sweep()
            except Exception:
                # The loop outliving one bad sweep is the whole point: a reaper
                # that dies on a transient database error is a reaper that is
                # not there when the next worker does.
                logger.exception("resume reaper sweep failed")

    def sweep(self) -> list[Recovery]:
        with self._session_scope() as db:
            return recover_abandoned_runs(db, queue=self._queue)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_healthy(self) -> bool:
        """Whether the loop is doing what configuration asked of it.

        Switched off by configuration it is healthy by definition. Switched on,
        it is healthy while its thread is alive — for the readiness probe
        (app/main.py), because a process whose reaper has stopped no longer
        recovers abandoned runs, and that is worth a 503 rather than a 200.
        """
        if not self._enabled or self._interval <= 0:
            return True
        return self._thread is not None and self._thread.is_alive()
