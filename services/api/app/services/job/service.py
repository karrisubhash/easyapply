"""The job application service.

Owns ingestion, identity/deduplication and starting a processing run. The
pipeline itself lives in `pipeline.py`; this module is what the API calls, the
same split resume draws between `resume.service` and `resume.pipeline`.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Job, JobProcessingRun, JobRequirements
from app.schemas.job import JobIngestionRequest
from app.services.errors import ConflictError, NotFoundError
from app.services.job import pipeline
from app.services.job.failures import is_user_retryable
from app.services.job.normalization import normalize_job_payload

logger = logging.getLogger(__name__)


def _load_job(db: Session, *, user_id: int, job_id: int) -> Job:
    job = db.scalar(
        select(Job)
        .where(Job.id == job_id, Job.user_id == user_id)
        .options(selectinload(Job.runs))
    )
    if job is None:
        raise NotFoundError("That job does not exist.")
    return job


def _find_existing(db: Session, *, user_id: int, payload: JobIngestionRequest, canonical_url: str) -> Job | None:
    """The read half of the two-tier identity policy on `Job`.

    Read-then-insert has an unavoidable race, which is why `ingest_job` below
    still catches the constraint violation on insert — this lookup is what
    makes the common case (the extension re-scraping a page it already sent)
    resolve without ever reaching for that fallback.
    """
    if payload.external_job_id:
        return db.scalar(
            select(Job).where(
                Job.user_id == user_id,
                Job.source_type == payload.source_type,
                Job.external_job_id == payload.external_job_id,
            )
        )
    return db.scalar(
        select(Job).where(
            Job.user_id == user_id,
            Job.source_type == payload.source_type,
            Job.external_job_id.is_(None),
            Job.canonical_url == canonical_url,
        )
    )


def ingest_job(db: Session, *, user_id: int, payload: JobIngestionRequest) -> tuple[Job, bool]:
    """Validate, normalize, and materialize a job — idempotently.

    Returns `(job, created)`. `created` is False when this exact identity
    already existed, in which case the existing row is returned UNCHANGED: if
    the underlying posting text has since edited, the first-seen version wins
    rather than being silently overwritten. Re-submission is for confirming a
    save succeeded, not for keeping a job's fields fresh — see
    `docs/job-intelligence.md` for why that tradeoff was made deliberately
    rather than by default.

    A new job is processed inline, in the same call: deterministic
    structuring is a regex pass over text already in memory, so there is
    nothing to gain by queuing it, and doing it here means a single POST
    reliably returns a job with its requirements already attached.
    """
    normalized = normalize_job_payload(payload)

    existing = _find_existing(db, user_id=user_id, payload=payload, canonical_url=normalized.canonical_url)
    if existing is not None:
        return existing, False

    job = Job(
        user_id=user_id,
        source_type=payload.source_type,
        source_url=payload.source_url,
        canonical_url=normalized.canonical_url,
        external_job_id=payload.external_job_id,
        content_hash=normalized.content_hash,
        job_title=normalized.job_title,
        company_name=normalized.company_name,
        location_raw=normalized.location_raw,
        employment_type=normalized.employment_type,
        workplace_type=normalized.workplace_type,
        description_text=normalized.description_text,
        salary_min=normalized.salary_min,
        salary_max=normalized.salary_max,
        salary_currency=normalized.salary_currency,
        salary_period=normalized.salary_period,
        raw_payload=payload.model_dump(mode="json"),
    )
    db.add(job)

    # The same race resume's upload_resume guards against: two requests for
    # the same identity landing between the SELECT above and this INSERT. The
    # savepoint makes recovery possible — a bare IntegrityError would abort
    # the whole transaction and poison the session for anything read
    # afterwards on it.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        winner = _find_existing(db, user_id=user_id, payload=payload, canonical_url=normalized.canonical_url)
        if winner is None:
            raise
        return winner, False

    run = JobProcessingRun(job_id=job.id, attempt=1)
    db.add(run)
    db.flush()
    db.commit()

    logger.info(
        "job.ingested",
        extra={"job_id": job.id, "source_type": job.source_type, "run_id": run.id},
    )

    pipeline.process_run(db, run_id=run.id)
    db.refresh(job)
    return job, True


def list_jobs(db: Session, *, user_id: int) -> list[Job]:
    return list(
        db.scalars(
            select(Job)
            .where(Job.user_id == user_id)
            .options(selectinload(Job.runs))
            .order_by(Job.created_at.desc())
        )
    )


def get_job(db: Session, *, user_id: int, job_id: int) -> Job:
    return _load_job(db, user_id=user_id, job_id=job_id)


def latest_run(job: Job) -> JobProcessingRun | None:
    return job.latest_run


def open_next_attempt(db: Session, *, job: Job) -> tuple[JobProcessingRun, bool]:
    """Create the next processing run for a job, or report losing the race.

    Attempt number is max()+1 over this job's runs, with
    UNIQUE(job_id, attempt) as the backstop if two reprocess requests race —
    the same discipline `resume.retry.open_next_attempt` uses, sized down: no
    backoff, because nothing here is worth delaying yet.

    Returns `(run, created)` rather than just a run. There is no lease on job
    runs — nothing here holds a lock the way `resume.lease` does — so if this
    returned a run unconditionally, BOTH sides of a lost race would go on to
    call `pipeline.process_run` on it from two different threads with nothing
    stopping them from interleaving their writes to the same row. Reporting
    `created=False` is what lets `reprocess` below refuse to drive a run it did
    not open — see it for the other half of this.

    The failed flush here is an outermost write, the same situation
    `resume.retry.open_next_attempt` hit — a nested savepoint does not protect
    the session when the failing statement is the transaction's first, and
    `db.rollback()` is what actually restores it. Verified against a real
    IntegrityError, not assumed.
    """
    highest = db.scalar(
        select(func.max(JobProcessingRun.attempt)).where(JobProcessingRun.job_id == job.id)
    )
    run = JobProcessingRun(job_id=job.id, attempt=(highest or 0) + 1)
    db.add(run)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        # `job.runs` was loaded before this race started and does not include
        # whatever the winner just inserted; expiring it is what makes the
        # next access a fresh SELECT rather than a stale cache hit.
        db.expire(job, ["runs"])
        return job.latest_run, False  # type: ignore[return-value]
    db.commit()
    return run, True


def require_retryable(run: JobProcessingRun | None) -> None:
    """Refuse a manual retry that cannot help.

    A person retrying a permanent failure — the AI provider rejected the
    content, the credentials are wrong, the response never fit the schema —
    would get the identical failure back: nothing about a job posting's own
    text changes between one attempt and the next. Telling them so is more
    useful than letting them watch it fail again. The mirror of
    `resume.service.require_retryable`, sized down: job processing is
    synchronous, so there is no "still in flight" case to refuse here — by the
    time a reprocess request returns, the attempt it started has already
    finished one way or the other.

    A failure caused by this deployment's configuration — no key, bad
    credentials, no credit — is NOT refused, although it is permanent: the
    posting did not cause it, the operator can cure it, and this is the only
    way the user can find out whether they have. See
    `failures.CONFIGURATION_ERROR_CODES` for the set and why each is in it.
    """
    if run is None or not run.is_terminal:
        return
    if run.status == "failed" and not is_user_retryable(run.error_code):
        raise ConflictError(
            "This job failed for a reason that will not change on another "
            "attempt without a configuration or code change."
        )


def reprocess(db: Session, *, job: Job) -> JobProcessingRun:
    require_retryable(job.latest_run)
    run, created = open_next_attempt(db, job=job)
    if not created:
        # Someone else already opened this attempt. Report its current state
        # rather than processing it a second time from a different thread with
        # no lock between them — see open_next_attempt's docstring.
        return run
    return pipeline.process_run(db, run_id=run.id)


def get_requirements_run(db: Session, *, job: Job) -> JobProcessingRun | None:
    """The run whose reading the review screen should show.

    The newest run that produced a `job_requirements` row — exactly the row
    `matching.service._load_requirements` matches against and tailoring pins —
    and only when there is none, the latest run, so a job still processing or
    whose every attempt failed can still show its state.

    It used to be `job.latest_run` unconditionally. After a failed reprocess
    that run has no requirements, so the review screen showed "nothing
    structured" while matching and tailoring went on using the previous
    completed reading: two surfaces describing the same job differently, and
    the one the user could see was the wrong one. The failed attempt is not
    hidden — the response's `job.latest_run` still reports it, and a
    `run.id` different from `job.latest_run.id` is the signal that a newer
    attempt did not replace this reading.

    Ordered by attempt, not by id or timestamp, for the same reason matching
    orders by it: attempt numbers are this job's own sequence.
    """
    structured = db.scalar(
        select(JobProcessingRun)
        .join(JobRequirements, JobRequirements.job_processing_run_id == JobProcessingRun.id)
        .where(JobProcessingRun.job_id == job.id)
        .order_by(JobProcessingRun.attempt.desc())
        .limit(1)
    )
    return structured if structured is not None else job.latest_run
