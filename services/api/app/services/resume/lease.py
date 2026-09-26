"""Claiming a processing run, in the database.

A lease is the answer to "who is working on this, and are they still alive?"
Both halves have to survive the worker that holds it, so both live in the row
rather than in any process's memory. A mutex, a local set of in-flight ids, or
an asyncio lock would all answer the first question and none of them the second:
after a worker is killed they simply forget, and the run is stuck.

Everything here is one statement. Acquisition is an UPDATE with the claim
conditions in its WHERE clause, so the database decides who wins — no read, no
gap, no window in which two workers both believe they are first. `rowcount` is
the verdict.

The transition trigger on this table is deliberately untouched by leases: every
statement here leaves `status` alone, which the trigger treats as progress
within a state rather than a transition.
"""

import logging
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import ResumeProcessingRun
from app.models.resume_processing import TERMINAL_STATUSES

logger = logging.getLogger(__name__)


def new_worker_id() -> str:
    """An identity that is unique per worker *process*, not per host.

    Host and pid make it legible in a log; the random suffix is what keeps two
    replicas of the same image, or a pid reused after a restart, from colliding.
    """
    return f"{socket.gethostname()}/{os.getpid()}/{secrets.token_hex(4)}"


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _is_due(moment: datetime):
    """The backoff condition, as one expression reused by every claim query.

    Written once because it is the difference between a backoff and a suggestion:
    any query that hands a run to a worker without it can start a retry early,
    and the bug would look like the backoff simply not working.
    """
    return (ResumeProcessingRun.next_retry_at.is_(None)) | (
        ResumeProcessingRun.next_retry_at <= moment
    )


def acquire(
    db: Session,
    *,
    run_id: int,
    owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Take the run for `owner`, or report that it is not ours to take.

    Claimable means: not terminal, past its backoff, and either unleased or
    holding a lease that has run out. Re-entrant for the current owner, so a
    worker that crashed after claiming but before finishing can pick its own run
    back up.

    The backoff check is here, in the statement that actually decides, rather
    than only in the query that finds work. Anything holding a run id may call
    this — a stale message, a queue that outlived a restart, an operator — and
    the run's own row is the only thing in a position to refuse.

    Commits. A lease nobody else can see is not a lease.
    """
    moment = _now(now)
    deadline = moment + timedelta(seconds=lease_seconds)

    result = db.execute(
        update(ResumeProcessingRun)
        .where(
            ResumeProcessingRun.id == run_id,
            ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
            _is_due(moment),
            # The claim condition, as one expression the database evaluates
            # while holding the row lock.
            (ResumeProcessingRun.leased_until.is_(None))
            | (ResumeProcessingRun.leased_until <= moment)
            | (ResumeProcessingRun.lease_owner == owner),
        )
        .values(lease_owner=owner, leased_until=deadline, heartbeat_at=moment)
        .execution_options(synchronize_session=False)
    )
    acquired = result.rowcount == 1
    db.commit()

    if acquired:
        logger.info(
            "resume.run.acquired",
            extra={
                "resume_run_id": run_id,
                "lease_owner": owner,
                "leased_until": deadline.isoformat(),
            },
        )
    else:
        logger.info(
            "resume.run.claim_refused",
            extra={"resume_run_id": run_id, "lease_owner": owner},
        )
    return acquired


def renew(
    db: Session,
    *,
    run_id: int,
    owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Push the lease out. False means we no longer hold it.

    False is the signal that matters: it means the run was reaped while we were
    working on it, and whatever this worker is doing is no longer authoritative.
    Ownership is part of the WHERE clause, so a worker can never renew a lease
    that has already been taken from it.
    """
    moment = _now(now)
    deadline = moment + timedelta(seconds=lease_seconds)

    result = db.execute(
        update(ResumeProcessingRun)
        .where(
            ResumeProcessingRun.id == run_id,
            ResumeProcessingRun.lease_owner == owner,
            ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
        )
        .values(leased_until=deadline, heartbeat_at=moment)
        .execution_options(synchronize_session=False)
    )
    renewed = result.rowcount == 1
    db.commit()

    logger.debug(
        "resume.run.heartbeat",
        extra={"resume_run_id": run_id, "lease_owner": owner, "renewed": renewed},
    )
    return renewed


def release(db: Session, *, run_id: int, owner: str) -> bool:
    """Give the run back, whatever state it ended in.

    `heartbeat_at` is left behind on purpose: the lease is over, but when the
    run last showed a sign of life is still worth knowing afterwards.
    """
    result = db.execute(
        update(ResumeProcessingRun)
        .where(
            ResumeProcessingRun.id == run_id,
            ResumeProcessingRun.lease_owner == owner,
        )
        .values(lease_owner=None, leased_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def claimable_run_ids(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[int]:
    """Runs a worker could take right now, oldest first.

    This is the queue read. It returns ids rather than rows because whatever a
    poller reads here is already stale by the time it acts — acquire() is what
    decides, and it decides again against the live row.
    """
    moment = _now(now)
    return list(
        db.scalars(
            select(ResumeProcessingRun.id)
            .where(
                ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
                _is_due(moment),
                (ResumeProcessingRun.leased_until.is_(None))
                | (ResumeProcessingRun.leased_until <= moment),
            )
            .order_by(ResumeProcessingRun.id)
            .limit(limit)
        )
    )


def expired_runs(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[ResumeProcessingRun]:
    """Non-terminal runs whose holder stopped renewing.

    Distinct from never-leased work: these were started by a worker that is now
    gone, so they carry partial progress the reaper has to account for.
    """
    moment = _now(now)
    return list(
        db.scalars(
            select(ResumeProcessingRun)
            .where(
                ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
                ResumeProcessingRun.leased_until.is_not(None),
                ResumeProcessingRun.leased_until <= moment,
            )
            .order_by(ResumeProcessingRun.id)
            .limit(limit)
        )
    )


def unclaimed_runs(
    db: Session,
    *,
    limit: int,
    older_than: datetime,
) -> list[ResumeProcessingRun]:
    """Runs nobody ever picked up.

    The enqueue was lost — an API process that committed the run and then
    restarted before its pool got to it. Nothing has been attempted, so there is
    no partial work to account for and nothing to fail; these are simply still
    queued, and the reaper puts them back on the queue.

    Deliberately excludes anything carrying a retry time. A scheduled retry has
    also never been picked up, and looks identical on every other column — but it
    is waiting on purpose, and calling that abandonment would mean the grace
    period silently overrode the backoff. Those are `due_retry_runs`.
    """
    return list(
        db.scalars(
            select(ResumeProcessingRun)
            .where(
                ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
                ResumeProcessingRun.lease_owner.is_(None),
                ResumeProcessingRun.heartbeat_at.is_(None),
                ResumeProcessingRun.next_retry_at.is_(None),
                ResumeProcessingRun.created_at <= older_than,
            )
            .order_by(ResumeProcessingRun.id)
            .limit(limit)
        )
    )


def due_retry_runs(
    db: Session,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[ResumeProcessingRun]:
    """Scheduled retries whose wait is over and which nobody is running.

    This is what turns a persisted timestamp back into work. A retry is created
    un-enqueued precisely because it must not run yet; something has to notice
    when that stops being true, and doing it by query means the noticing
    survives a restart, needs no timer, and cannot be lost with a message.

    No grace period, unlike orphan recovery: the backoff *was* the grace period,
    and adding another would silently lengthen every delay an operator
    configured.
    """
    moment = _now(now)
    return list(
        db.scalars(
            select(ResumeProcessingRun)
            .where(
                ResumeProcessingRun.status.not_in(TERMINAL_STATUSES),
                ResumeProcessingRun.next_retry_at.is_not(None),
                ResumeProcessingRun.next_retry_at <= moment,
                # Unleased only. A run someone already picked up this instant is
                # not waiting for anything.
                (ResumeProcessingRun.leased_until.is_(None))
                | (ResumeProcessingRun.leased_until <= moment),
            )
            .order_by(ResumeProcessingRun.next_retry_at)
            .limit(limit)
        )
    )
