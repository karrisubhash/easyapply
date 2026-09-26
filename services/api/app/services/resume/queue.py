"""Getting a processing run to a worker.

The queue's whole contract is `enqueue(run_id)`. It carries an integer, never a
document, never a session, never a candidate — which is what will let a hosted
broker replace the default implementation without the resume domain noticing.

There is no queue table. A run that is not terminal and not leased *is*
outstanding work, so the database already holds the queue, and a second table
listing the same facts could only ever disagree with it. That is also why
enqueueing is advisory: losing a message costs latency, not work, because the
run is still there to be found by a poll or by the reaper.

Three backends, chosen by configuration:

  * `ThreadPoolQueue` — the default. Makes processing asynchronous without
    adding infrastructure: the API returns as soon as the run is committed, and
    a pool thread picks it up.
  * `ExternalQueue` — enqueue is a no-op and a separate `python -m app.worker`
    process claims runs from the database. This is the shape a hosted queue
    will take, and it is what the API runs with when workers are deployed
    separately.
  * `InlineQueue` — executes on the calling thread. For tests that want the
    pipeline to have run by the time enqueue returns, and for diagnostics.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app.core.config import settings
from app.services.resume.worker import ResumeWorker

logger = logging.getLogger(__name__)


class ProcessingQueue(Protocol):
    """What the API is allowed to know about how work reaches a worker."""

    def enqueue(self, run_id: int) -> bool:
        """Offer a run for processing.

        True when this call put the run on the queue, False when it was already
        there. Callers do not need the answer — the lease makes a duplicate
        harmless — but it makes "queued exactly once" observable.
        """
        ...

    def shutdown(self, *, wait: bool = True) -> None: ...


class InlineQueue:
    """Runs the work on the calling thread."""

    def __init__(self, worker: ResumeWorker) -> None:
        self._worker = worker
        worker.queue = self

    def enqueue(self, run_id: int) -> bool:
        logger.info("resume.queue.enqueued", extra={"resume_run_id": run_id, "queue": "inline"})
        self._worker.execute(run_id)
        return True

    def shutdown(self, *, wait: bool = True) -> None:
        return None


class ExternalQueue:
    """Records the intent and leaves the work to a separate worker process.

    Enqueue is a no-op because the run row is the queue: committing it is the
    enqueue. A broker-backed implementation would publish here, and the no-op
    version is what proves the design does not depend on the message arriving.
    """

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue(self, run_id: int) -> bool:
        self.enqueued.append(run_id)
        logger.info(
            "resume.queue.enqueued", extra={"resume_run_id": run_id, "queue": "external"}
        )
        return True

    def shutdown(self, *, wait: bool = True) -> None:
        return None


class ThreadPoolQueue:
    """An in-process pool.

    Deduplication is by run id and is about work, not about correctness: two
    workers cannot process one run whatever this set says, because the lease
    decides. What it buys is that a retried request, or a reaper and an API both
    noticing the same run, do not each occupy a thread to discover that.

    The id is held for the whole time the run is in this queue's hands —
    admitted until finished, not merely until started. A run that is currently
    executing is not outstanding work, and admitting it again would buy a second
    thread whose only job is to fail to acquire the lease. Nothing legitimately
    needs the same id re-queued while it runs: a retry is always a new run with
    a new id, and the reaper only re-queues runs that were never claimed.
    """

    def __init__(self, worker: ResumeWorker, *, max_workers: int | None = None) -> None:
        self._worker = worker
        worker.queue = self
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers or settings.RESUME_QUEUE_MAX_WORKERS,
            thread_name_prefix="resume-worker",
        )
        self._pending: set[int] = set()
        self._guard = threading.Lock()
        self._closed = False

    def enqueue(self, run_id: int) -> bool:
        with self._guard:
            if self._closed:
                logger.warning(
                    "resume.queue.rejected",
                    extra={"resume_run_id": run_id, "reason": "shutting_down"},
                )
                return False
            if run_id in self._pending:
                logger.info(
                    "resume.queue.duplicate", extra={"resume_run_id": run_id}
                )
                return False
            self._pending.add(run_id)

        logger.info(
            "resume.queue.enqueued", extra={"resume_run_id": run_id, "queue": "thread"}
        )
        self._executor.submit(self._run, run_id)
        return True

    def _run(self, run_id: int) -> None:
        try:
            self._worker.execute(run_id)
        except Exception:
            # Nothing above this catches: a pool thread's exception is otherwise
            # swallowed into a Future nobody reads. The run itself is still
            # recoverable — its lease expires and the reaper finds it.
            logger.exception("resume worker failed for run %s", run_id)
        finally:
            # In a finally, so a worker that raised does not leave its id
            # admitted forever and the run permanently un-queueable.
            with self._guard:
                self._pending.discard(run_id)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._guard:
            self._closed = True
        self._executor.shutdown(wait=wait)


def build_queue(
    backend: str | None = None, worker: ResumeWorker | None = None
) -> ProcessingQueue:
    """The configured queue, with a worker wired to it."""
    selected = (backend or settings.RESUME_QUEUE_BACKEND or "thread").strip().lower()

    if selected == "external":
        return ExternalQueue()

    resolved = worker or ResumeWorker()
    if selected == "inline":
        return InlineQueue(resolved)
    if selected == "thread":
        return ThreadPoolQueue(resolved)

    raise ValueError(f"unknown RESUME_QUEUE_BACKEND {selected!r}")
