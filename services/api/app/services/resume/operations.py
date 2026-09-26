"""What resume processing looks like to someone responsible for it.

One question: which documents need a person, and which are simply in progress?
Everything here is derived from `resume_processing_runs` — there is no operator
status column, no flag set when a retry is exhausted, nothing a background job
has to keep in step. The runs already record every fact this needs, and a second
place to store the same conclusion is a second place for it to be wrong.

The six states a document can be in, and what each one means to an operator:

  COMPLETED       the latest attempt produced a draft. Nothing to do.
  PROCESSING      an attempt is running, or is queued and due. Nothing to do
                  yet — this is what working looks like.
  WAITING_RETRY   an attempt is scheduled and its backoff has not elapsed.
                  Nothing to do; the system is already handling it.
  EXHAUSTED       the last failure was worth retrying and the attempt budget is
                  spent. THIS IS THE ONE THAT NEEDS A PERSON: something outside
                  the document kept failing and nothing will try again.
  FAILED_PERMANENT the document itself cannot be processed — wrong format,
                  corrupt, encrypted, a scan with no OCR engine. A person can
                  tell the user, but no amount of retrying helps.
  STALLED         a non-terminal run that is past due and nobody has taken.
                  Should be transient — the reaper collects these — so seeing
                  one persist means the reaper or the workers are not running.

The distinction that matters most is EXHAUSTED versus FAILED_PERMANENT. Both are
"failed and not retrying", and conflating them would send an operator to look at
a provider outage when the real answer is that someone uploaded a photograph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ResumeDocument, ResumeProcessingRun
from app.models.resume_processing import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    TERMINAL_STATUSES,
)
from app.services.errors import NotFoundError
from app.services.resume.failures import classification, is_retryable

logger = logging.getLogger(__name__)

COMPLETED = "completed"
PROCESSING = "processing"
WAITING_RETRY = "waiting_retry"
EXHAUSTED = "exhausted"
FAILED_PERMANENT = "failed_permanent"
STALLED = "stalled"

#: The states an operator is being asked to do something about. Everything else
#: is the system working, and a view that showed it all would bury these.
NEEDS_ATTENTION = frozenset({EXHAUSTED, FAILED_PERMANENT, STALLED})


@dataclass(frozen=True)
class DocumentProcessingState:
    """One document's processing, as an operator needs to see it.

    Carries no resume content by construction: the fields are ids, counts,
    timestamps and a closed vocabulary of codes. See `app/schemas/operations.py`
    for why that is enforced by building the response field by field rather than
    by filtering a model that already holds the text.
    """

    document_id: int
    user_id: int
    state: str
    attempts_used: int
    max_attempts: int
    latest_run_id: int | None
    status: str | None
    failed_stage: str | None
    error_code: str | None
    failure_classification: str | None
    document_created_at: datetime
    run_created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    next_retry_at: datetime | None
    heartbeat_at: datetime | None
    leased: bool


def derive_state(
    run: ResumeProcessingRun | None,
    *,
    attempts_used: int,
    now: datetime,
    max_attempts: int | None = None,
) -> str:
    """Which of the six states a document is in, from its latest run.

    A pure function of the run plus two numbers, so the rule can be tested
    directly and the API, the UI and any future alert all reach the same verdict
    from the same place.
    """
    ceiling = max_attempts if max_attempts is not None else settings.RESUME_MAX_ATTEMPTS

    if run is None:
        # A document with no run at all. Upload commits the document and the run
        # together, so this means something interrupted that — rare, and worth
        # an operator's attention rather than a silent "fine".
        return STALLED

    if run.status == STATUS_COMPLETED:
        return COMPLETED

    if run.status == STATUS_FAILED:
        if not is_retryable(run.error_code):
            # Permanent, or a code nobody has classified — which the retry
            # policy treats as permanent on purpose.
            return FAILED_PERMANENT
        if attempts_used >= ceiling:
            return EXHAUSTED
        # Worth retrying, budget remains, and no successor exists yet. The
        # scheduler is between the failure and the retry, or it failed to open
        # one — either way nobody is currently working on this document.
        return STALLED

    # Non-terminal from here: uploaded, validating, extracting, ... A run that
    # is leased is being worked on, whatever else is true of it.
    if run.is_leased_at(now):
        return PROCESSING

    if not run.is_due_at(now):
        return WAITING_RETRY

    if run.started_at is not None:
        # Started, unleased, and due: the worker let go without finishing. The
        # reaper's expired-lease pass collects these.
        return STALLED

    # Queued and due, nobody holding it. Normal for the seconds between an
    # upload and a worker picking it up; the caller decides how long is too long.
    return PROCESSING


def _latest_runs_subquery():
    """The most recent run per document, by attempt.

    Attempt rather than created_at: attempts are unique per document and
    strictly ordered, while two runs created in the same millisecond would make
    a timestamp ordering arbitrary.
    """
    return (
        select(
            ResumeProcessingRun.resume_document_id.label("document_id"),
            func.max(ResumeProcessingRun.attempt).label("latest_attempt"),
            func.count(ResumeProcessingRun.id).label("attempts_used"),
        )
        .group_by(ResumeProcessingRun.resume_document_id)
        .subquery()
    )


def document_states(
    db: Session,
    *,
    states: frozenset[str] | set[str] | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[DocumentProcessingState]:
    """Every document's processing state, newest first, optionally filtered.

    Deliberately NOT scoped to a caller. This is the one place in the
    application that reads across users, which is exactly why the endpoint in
    front of it carries its own authorization and why this returns a dataclass
    of scalars rather than ORM rows — an ORM row would carry relationships to
    page text and structured payloads, one attribute access away from a
    cross-user leak.
    """
    moment = now or datetime.now(timezone.utc)
    latest = _latest_runs_subquery()

    rows = db.execute(
        select(
            ResumeDocument.id,
            ResumeDocument.user_id,
            ResumeDocument.created_at,
            latest.c.attempts_used,
            ResumeProcessingRun,
        )
        .join(latest, latest.c.document_id == ResumeDocument.id, isouter=True)
        .join(
            ResumeProcessingRun,
            (ResumeProcessingRun.resume_document_id == ResumeDocument.id)
            & (ResumeProcessingRun.attempt == latest.c.latest_attempt),
            isouter=True,
        )
        .order_by(ResumeDocument.id.desc())
    ).all()

    found: list[DocumentProcessingState] = []
    for document_id, user_id, document_created_at, attempts, run in rows:
        used = attempts or 0
        state = derive_state(run, attempts_used=used, now=moment)
        if states is not None and state not in states:
            continue
        found.append(
            DocumentProcessingState(
                document_id=document_id,
                user_id=user_id,
                state=state,
                attempts_used=used,
                max_attempts=settings.RESUME_MAX_ATTEMPTS,
                latest_run_id=run.id if run else None,
                status=run.status if run else None,
                failed_stage=run.failed_stage if run else None,
                error_code=run.error_code if run else None,
                failure_classification=(
                    classification(run.error_code) if run and run.error_code else None
                ),
                document_created_at=document_created_at,
                run_created_at=run.created_at if run else None,
                started_at=run.started_at if run else None,
                finished_at=run.finished_at if run else None,
                next_retry_at=run.next_retry_at if run else None,
                heartbeat_at=run.heartbeat_at if run else None,
                leased=bool(run and run.is_leased_at(moment)),
            )
        )
        if len(found) >= limit:
            break

    return found


def needs_attention(
    db: Session, *, limit: int = 100, now: datetime | None = None
) -> list[DocumentProcessingState]:
    """Only what an operator is being asked to do something about."""
    return document_states(db, states=NEEDS_ATTENTION, limit=limit, now=now)


def document_attempts(
    db: Session, *, document_id: int
) -> tuple[ResumeDocument, list[ResumeProcessingRun]]:
    """Every attempt for one document, oldest first.

    The whole history, because the point of never reopening a run is that the
    history is the diagnosis: three attempts failing with the same provider code
    is a different problem from three failing with different ones.
    """
    document = db.get(ResumeDocument, document_id)
    if document is None:
        raise NotFoundError("That resume does not exist.")

    runs = list(
        db.scalars(
            select(ResumeProcessingRun)
            .where(ResumeProcessingRun.resume_document_id == document_id)
            .order_by(ResumeProcessingRun.attempt)
        )
    )
    return document, runs


def get_run(db: Session, *, run_id: int) -> ResumeProcessingRun:
    run = db.get(ResumeProcessingRun, run_id)
    if run is None:
        raise NotFoundError("That processing run does not exist.")
    return run


#: The only keys of provider_metadata an operator response may carry.
#:
#: An allowlist rather than a denylist because the column is unvalidated JSONB
#: written by provider adapters, and a future adapter returning a richer debug
#: object would otherwise have it served verbatim across every user. Notably
#: absent: `response_id`, which is not content but is a handle into the vendor's
#: own record of a request whose body was the resume.
SAFE_PROVIDER_METADATA_KEYS = frozenset(
    {
        "latency_ms",
        "model",
        "stop_reason",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def safe_provider_metadata(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project provider metadata down to what is safe to show."""
    if not raw:
        return None
    projected = {k: v for k, v in raw.items() if k in SAFE_PROVIDER_METADATA_KEYS}
    return projected or None
