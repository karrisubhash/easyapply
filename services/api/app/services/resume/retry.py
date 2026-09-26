"""Deciding whether to try again, when, and opening the attempt that does.

A retry here is always a NEW run. Reopening the failed one would overwrite the
record of what went wrong, make the state graph cyclic, and turn "how many times
did this fail, and how?" from a query into an archaeology problem. So attempt 1
stays failed forever and attempt 2 starts beside it, which is what makes the
history — failed, failed, completed — a thing the database holds rather than a
thing we claim.

The successor is created immediately and carries its own start time. That is the
whole of the backoff: nothing sleeps, nothing is scheduled anywhere, and a run
that is not yet due is simply a row the claim queries will not return. It
survives a restart because it is a row, and two workers cannot both act on it
because claiming it is still one atomic UPDATE.

    failure
      └─ classified transient? ──no──▶ skipped   (permanent, or unclassified)
            │yes
            └─ attempts remain? ──no──▶ exhausted (budget spent, needs a person)
                  │yes
                  └─ scheduled  run N+1 created, next_retry_at = now + backoff
                        └─ eligible   the reaper enqueues it once that passes
                              └─ new attempt runs

Two policies, deliberately different:

  * Automatic retries are bounded by RESUME_MAX_ATTEMPTS, only fire for
    transient failures, and wait. Nobody is watching, so the ceiling is what
    stops a permanently broken document consuming a worker forever and the
    backoff is what stops it consuming one *quickly*.
  * A person clicking "try again" is not bounded by that ceiling and does not
    wait. They have information the system does not — the outage is over — and
    one click is not a runaway. What they cannot do is retry a failure that the
    same bytes will reproduce; see `manual_retry_refusal`. A failure the
    DEPLOYMENT caused is not one of those — no OCR engine, a revoked key — and
    a person may retry it once someone has fixed the deployment; see
    `failures.DEPLOYMENT_ERROR_CODES`. The automatic policy is unchanged: it
    still never retries those, because nothing about them changes on its own.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ResumeDocument, ResumeProcessingRun
from app.services.resume.backoff import BackoffPolicy, policy
from app.services.resume.failures import (
    classification,
    is_retryable,
    may_retry_manually,
)

logger = logging.getLogger(__name__)


def attempts_used(db: Session, *, document_id: int) -> int:
    """How many attempts this document has consumed.

    Read from the rows rather than kept as a counter on the document: the rows
    are the record, and a counter beside them is a second answer that can drift.
    """
    return db.scalar(
        select(func.count(ResumeProcessingRun.id)).where(
            ResumeProcessingRun.resume_document_id == document_id
        )
    ) or 0


def may_retry_automatically(
    db: Session, *, document_id: int, error_code: str | None
) -> bool:
    """Whether the system should open another attempt without being asked."""
    if not is_retryable(error_code):
        return False
    return attempts_used(db, document_id=document_id) < settings.RESUME_MAX_ATTEMPTS


def is_exhausted(
    db: Session, *, document_id: int, error_code: str | None
) -> bool:
    """Whether a retry was warranted and the budget is what refused it.

    The distinction the operator view is built on: a document that failed for a
    reason worth retrying, and has simply run out of attempts, needs a person.
    One that failed permanently needs a different file.
    """
    if not is_retryable(error_code):
        return False
    return attempts_used(db, document_id=document_id) >= settings.RESUME_MAX_ATTEMPTS


def manual_retry_refusal(run: ResumeProcessingRun | None) -> str | None:
    """Why a person may not retry this run, or None if they may.

    Returns the reason rather than a bool so the caller can say it out loud. A
    refusal is only ever about whether another attempt could differ, never about
    how many have already been made — and never about a backoff, which exists to
    pace a machine that is not watching, not a person who is.
    """
    if run is None:
        return None
    if not run.is_terminal:
        if run.next_retry_at is not None and run.started_at is None:
            # Waiting out a backoff, not being worked on. Saying "already being
            # processed" would be untrue, and the difference is the one thing a
            # person in this position actually wants to know.
            return (
                "A retry for this resume is already scheduled and has not run "
                "yet. Wait for it to finish before asking for another."
            )
        return "This resume is already being processed."
    if run.status == "failed" and not may_retry_manually(run.error_code):
        # Only a failure the document itself causes. One this deployment caused
        # — no OCR engine, a revoked key, a bug of ours — is allowed through:
        # re-uploading the same bytes resolves to this same document, so a
        # refusal here was the end of the road for it. See
        # `failures.DEPLOYMENT_ERROR_CODES`.
        return (
            "This resume failed for a reason that will not change on another "
            "attempt. Upload a corrected file instead."
        )
    return None


def open_next_attempt(
    db: Session,
    *,
    document: ResumeDocument,
    attempt: int | None = None,
    next_retry_at: datetime | None = None,
) -> ResumeProcessingRun | None:
    """Create a run for a document, or None if that attempt already exists.

    `attempt` is passed explicitly by the retry path, which knows exactly which
    successor it means; left out, it is max()+1, which is what a person asking
    for another attempt means. The difference matters under concurrency — see
    `schedule_retry`.

    Losing the race is recovered with a full rollback, NOT a savepoint. A
    savepoint looks like the right tool and is not: when the failing flush is
    the outermost work on the session, rolling back to the savepoint still
    leaves the session inactive, and the next statement raises
    PendingRollbackError instead of the caller's own error. Measured, not
    assumed.

    That makes one precondition load-bearing, and every caller meets it: there
    must be no uncommitted work on this session, because the rollback discards
    it. Both call sites only read before getting here.
    """
    if attempt is None:
        highest = db.scalar(
            select(func.max(ResumeProcessingRun.attempt)).where(
                ResumeProcessingRun.resume_document_id == document.id
            )
        )
        attempt = (highest or 0) + 1

    run = ResumeProcessingRun(
        resume_document_id=document.id,
        attempt=attempt,
        next_retry_at=next_retry_at,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # UNIQUE(resume_document_id, attempt) refused it: another scheduler,
        # another reaper, or a person got to this attempt number first. Theirs
        # is as good as ours would have been.
        #
        # The rollback is what makes the session usable again — and it also
        # detaches the pending row, so there is nothing left to clean up.
        db.rollback()
        return None

    db.commit()
    logger.info(
        "resume.run.created",
        extra={
            "resume_run_id": run.id,
            "resume_document_id": document.id,
            "attempt": run.attempt,
            "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
        },
    )
    return run


def schedule_retry(
    db: Session,
    *,
    failed_run: ResumeProcessingRun,
    queue=None,
    now: datetime | None = None,
    backoff: BackoffPolicy | None = None,
) -> ResumeProcessingRun | None:
    """Open the next attempt, delayed, if policy allows one.

    Returns the new run, or None when the failure was permanent, the budget is
    spent, or another process already opened this attempt. Either way the failed
    run is left exactly as it is.

    The successor's number is `failed_run.attempt + 1` rather than max()+1, and
    that is the concurrency guarantee: two processes reacting to the same
    failure compute the same number, so UNIQUE(resume_document_id, attempt) lets
    exactly one insert win. Deriving it from max() instead would let them
    compute 2 and 3 and both succeed — two retries for one failure, and an
    attempt silently burned.
    """
    moment = now or datetime.now(timezone.utc)
    document_id = failed_run.resume_document_id
    error_code = failed_run.error_code
    context = {
        "resume_run_id": failed_run.id,
        "resume_document_id": document_id,
        "attempt": failed_run.attempt,
        "failure_code": error_code,
        "classification": classification(error_code),
    }

    if not is_retryable(error_code):
        # Permanent, or a code nobody has classified — which failures.py treats
        # as permanent on purpose.
        logger.info("resume.retry.skipped", extra={**context, "reason": "not_retryable"})
        return None

    used = attempts_used(db, document_id=document_id)
    if used >= settings.RESUME_MAX_ATTEMPTS:
        # Worth retrying, and out of budget. A different event from `skipped`
        # because it means something different operationally: this document
        # needs a person, not a better file.
        logger.warning(
            "resume.retry.exhausted",
            extra={**context, "attempts_used": used, "max_attempts": settings.RESUME_MAX_ATTEMPTS},
        )
        return None

    document = db.get(ResumeDocument, document_id)
    if document is None:  # pragma: no cover - the FK makes this unreachable
        return None

    attempt = failed_run.attempt + 1
    next_retry_at = (backoff or policy()).next_retry_at(attempt, now=moment)

    retry = open_next_attempt(
        db, document=document, attempt=attempt, next_retry_at=next_retry_at
    )
    if retry is None:
        logger.info(
            "resume.retry.skipped",
            extra={**context, "reason": "already_scheduled", "next_attempt": attempt},
        )
        return None

    delay_seconds = (next_retry_at - moment).total_seconds() if next_retry_at else 0.0
    logger.info(
        "resume.retry.scheduled",
        extra={
            **context,
            "retry_run_id": retry.id,
            "retry_attempt": retry.attempt,
            "next_retry_at": next_retry_at.isoformat() if next_retry_at else None,
            "delay_seconds": round(delay_seconds, 3),
            "attempts_used": used,
            "max_attempts": settings.RESUME_MAX_ATTEMPTS,
        },
    )

    # Enqueued only if it is already due — which it is exactly when backoff is
    # switched off. Otherwise it is left as a row, and the reaper picks it up
    # when the time comes. Handing a not-yet-due run to a queue would mean
    # relying on the worker to refuse it, and something that must be refused
    # should not have been offered.
    if queue is not None and next_retry_at is None:
        queue.enqueue(retry.id)

    return retry
