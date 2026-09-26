"""Getting a tailoring run to a worker.

The contract is `enqueue(run_id)` — an integer, never a session and never a
document. The same shape `app/services/resume/queue.py` defines, and kept
separate rather than shared because the two carry different work: resume
processing is IO-heavy and reads files, tailoring is provider-bound and reads
the database, and a deployment will want to size and fail them independently.
Sharing the pool would mean a stuck resume starves a tailoring request.

There is no queue table, for the same reason there is not one over there: a run
that is not terminal IS outstanding work, so the database already holds the
queue and a second list of the same facts could only disagree with it.
Enqueueing is advisory — losing the message costs the user a click on "try
again", not the work.

That sentence was only half true while `service._in_flight` treated every
non-terminal run as going: the thread pool below lives in the API process, a
restart drops whatever it held, and the click on "Generate again" was answered
with the lost run itself, forever. It is true now because
`service._abandon_if_unattended` fails a stalled run no worker holds and opens
a new attempt, and because a lost message that turns up late finds its run
terminal and does nothing.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app.core.config import settings
from app.services.tailoring.worker import TailoringWorker

logger = logging.getLogger(__name__)


class TailoringQueue(Protocol):
    """What the API is allowed to know about how work reaches a worker."""

    def enqueue(self, run_id: int) -> bool: ...

    def shutdown(self, *, wait: bool = True) -> None: ...


class InlineTailoringQueue:
    """Runs the work on the calling thread.

    For tests that want the pipeline to have run by the time enqueue returns,
    and for diagnostics.
    """

    def __init__(self, worker: TailoringWorker) -> None:
        self._worker = worker
        worker.queue = self

    def enqueue(self, run_id: int) -> bool:
        logger.info(
            "tailoring.queue.enqueued", extra={"tailoring_run_id": run_id, "queue": "inline"}
        )
        self._worker.execute(run_id)
        return True

    def shutdown(self, *, wait: bool = True) -> None:
        return None


class ThreadPoolTailoringQueue:
    """An in-process pool.

    Deduplication is by run id and is about not wasting a thread, not about
    correctness: the pipeline refuses a run that is already terminal, so a
    duplicate delivery is harmless whatever this set says.
    """

    def __init__(self, worker: TailoringWorker, *, max_workers: int | None = None) -> None:
        self._worker = worker
        worker.queue = self
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers or settings.TAILORING_QUEUE_MAX_WORKERS,
            thread_name_prefix="tailoring-worker",
        )
        self._pending: set[int] = set()
        self._guard = threading.Lock()
        self._closed = False

    def enqueue(self, run_id: int) -> bool:
        with self._guard:
            if self._closed:
                logger.warning(
                    "tailoring.queue.rejected",
                    extra={"tailoring_run_id": run_id, "reason": "shutting_down"},
                )
                return False
            if run_id in self._pending:
                logger.info("tailoring.queue.duplicate", extra={"tailoring_run_id": run_id})
                return False
            self._pending.add(run_id)

        logger.info(
            "tailoring.queue.enqueued", extra={"tailoring_run_id": run_id, "queue": "thread"}
        )
        self._executor.submit(self._run, run_id)
        return True

    def _run(self, run_id: int) -> None:
        try:
            self._worker.execute(run_id)
        except Exception:
            logger.exception("tailoring worker failed for run %s", run_id)
        finally:
            # In a finally, so a worker that raised does not leave its id
            # admitted forever and the run permanently un-queueable.
            with self._guard:
                self._pending.discard(run_id)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._guard:
            self._closed = True
        self._executor.shutdown(wait=wait)


def build_tailoring_queue(
    backend: str | None = None, worker: TailoringWorker | None = None
) -> TailoringQueue:
    """The configured queue, with a worker wired to it."""
    selected = (backend or settings.TAILORING_QUEUE_BACKEND or "thread").strip().lower()
    resolved = worker or TailoringWorker()

    if selected == "inline":
        return InlineTailoringQueue(resolved)
    if selected == "thread":
        return ThreadPoolTailoringQueue(resolved)

    raise ValueError(f"unknown TAILORING_QUEUE_BACKEND {selected!r}")
