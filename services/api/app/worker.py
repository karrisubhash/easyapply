"""The standalone worker process: `python -m app.worker`.

Run this alongside the API with RESUME_QUEUE_BACKEND=external and the API stops
processing anything itself — it commits runs, and this claims them. That is the
deployment shape a hosted queue will arrive into, and running it today is what
proves the seam holds: the API does not import the worker, the worker does not
import the API, and the only thing they share is a table.

The loop is a poll rather than a subscription because the run table already
knows what is outstanding. When a broker lands, `_claim_and_run` becomes a
message handler and everything else here stays as it is.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading

from app.core.config import settings
from app.services.resume.reaper import ReaperLoop
from app.services.resume.worker import ResumeWorker

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    worker = ResumeWorker()
    stopping = threading.Event()

    def request_stop(signum, _frame):
        # Finish the run in hand, then exit. Killing mid-run is survivable —
        # the lease expires and the reaper recovers it — but finishing is
        # cheaper for everyone.
        logger.info("resume.worker.stopping", extra={"signal": signum})
        stopping.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, request_stop)

    reaper = ReaperLoop()
    reaper.start()

    logger.info(
        "resume.worker.started",
        extra={
            "worker_id": worker.worker_id,
            "poll_seconds": settings.RESUME_WORKER_POLL_SECONDS,
            "batch": settings.RESUME_WORKER_BATCH,
        },
    )

    try:
        while not stopping.is_set():
            try:
                outcomes = worker.poll_once()
            except Exception:
                # A failed poll is a bad minute for the database, not a reason
                # to exit and let a supervisor restart-loop us.
                logger.exception("resume worker poll failed")
                outcomes = []

            # Only wait when there was nothing to do. A busy queue should not be
            # drained at one batch per poll interval.
            if not outcomes:
                stopping.wait(settings.RESUME_WORKER_POLL_SECONDS)
    finally:
        reaper.stop()
        logger.info("resume.worker.stopped", extra={"worker_id": worker.worker_id})

    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
