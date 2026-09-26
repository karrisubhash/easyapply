"""Driving one processing run through the job state machine.

The entry point is `process_run(db, *, run_id=...)` — deliberately the same
shape as `app.services.resume.pipeline.process_run`: it takes an integer and
re-reads everything else from the database, touching nothing held in the
caller's memory. That is not needed for anything today, since this is called
synchronously from the request that created the run, but it is what a worker
would need if this ever moves off the request thread, and getting the shape
right now means that move is a change to the CALLER, not to this function.

Every stage transition goes through `advance()`, which refuses an illegal move
before the database's own CHECK constraints would, and every stage is timed
into `job_processing_events` — the same "which stage failed, how long did it
take" discipline the resume pipeline uses, for the same reason: those are
questions a query should answer, not a log search.
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Job, JobProcessingEvent, JobProcessingRun, JobRequirements
from app.models.job import (
    ALLOWED_TRANSITIONS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
)
from app.services.job.failures import classification, is_retryable
from app.services.job.providers import ProviderError
from app.services.job.structuring import (
    JobStructurer,
    StructuringError,
    get_job_structurer,
    verify_job_provenance,
)

logger = logging.getLogger(__name__)

#: Never str(exception) — a structuring bug can quote its input, and its input
#: is the job description. Truncated to match the column's own limit.
_MAX_ERROR_MESSAGE = 400

#: Which stage a run was in when its status alone doesn't say — used only by
#: the generic-failure fallback below, the same role
#: `resume.pipeline.STAGE_FOR_STATUS` plays.
_STAGE_FOR_STATUS = {
    STATUS_PROCESSING: "structure",
}


class ProcessingFailed(Exception):
    """A stage failed. Carries where and why, in our own words."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message[:_MAX_ERROR_MESSAGE]


class IllegalTransition(RuntimeError):
    pass


def advance(run: JobProcessingRun, status: str) -> None:
    if status not in ALLOWED_TRANSITIONS.get(run.status, frozenset()):
        raise IllegalTransition(f"{run.status} -> {status} is not a legal transition")
    run.status = status


def _record(
    db: Session,
    run: JobProcessingRun,
    *,
    stage: str,
    outcome: str,
    duration_ms: int | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        JobProcessingEvent(
            job_processing_run_id=run.id,
            stage=stage,
            outcome=outcome,
            duration_ms=duration_ms,
            detail=detail,
        )
    )
    logger.info(
        "job.stage",
        extra={
            "job_run_id": run.id,
            "job_id": run.job_id,
            "stage": stage,
            "outcome": outcome,
            "duration_ms": duration_ms,
        },
    )


@contextmanager
def _stage(db: Session, run: JobProcessingRun, stage: str):
    """Time a stage and record how it ended, whatever happens."""
    started = time.perf_counter()
    _record(db, run, stage=stage, outcome="started")
    try:
        yield
    except ProcessingFailed as failure:
        _record(
            db,
            run,
            stage=stage,
            outcome="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"code": failure.code, "classification": classification(failure.code)},
        )
        raise
    except Exception as exc:
        # Anything we did not anticipate is still this stage's failure. A
        # structuring bug or a database error left uncaught here would leave
        # the run stuck non-terminal — see fail_run's caller for the outer
        # safety net that guarantees a terminal state even if THIS re-raise is
        # somehow missed.
        logger.exception("job stage %s raised", stage)
        _record(
            db,
            run,
            stage=stage,
            outcome="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail={"code": "unexpected_error", "classification": classification("unexpected_error")},
        )
        raise ProcessingFailed(stage, "unexpected_error", "Processing stopped unexpectedly.") from exc
    else:
        _record(
            db,
            run,
            stage=stage,
            outcome="succeeded",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def fail_run(db: Session, run: JobProcessingRun, failure: ProcessingFailed) -> None:
    run.failed_stage = failure.stage
    run.error_code = failure.code
    run.error_message = failure.message
    run.finished_at = datetime.now(timezone.utc)
    advance(run, STATUS_FAILED)
    logger.warning(
        "job.run.failed",
        extra={
            "job_run_id": run.id,
            "job_id": run.job_id,
            "stage": failure.stage,
            "error_code": failure.code,
            "classification": classification(failure.code),
            "retryable": is_retryable(failure.code),
        },
    )


def process_run(
    db: Session,
    *,
    run_id: int,
    structurer: JobStructurer | None = None,
) -> JobProcessingRun:
    """Run the pipeline for one run id, start to finish.

    Called synchronously today — see the module docstring — but the id-only
    signature is what a worker would need, so introducing one later changes
    only the caller.
    """
    run = db.get(JobProcessingRun, run_id)
    if run is None:
        raise LookupError(f"job processing run {run_id} does not exist")

    job = db.get(Job, run.job_id)
    if job is None:
        raise LookupError(f"job {run.job_id} does not exist")

    structurer = structurer or get_job_structurer()

    run.started_at = datetime.now(timezone.utc)
    advance(run, STATUS_PROCESSING)
    db.flush()

    try:
        result = _run_structuring(db, run, job, structurer)
        _run_materialise(db, run, result)
    except ProcessingFailed as failure:
        try:
            fail_run(db, run, failure)
            db.commit()
        except Exception:
            logger.exception("recording the failure of job run %s failed", run_id)
            db.rollback()
            _abort(db, run_id, failure)
        return run
    except Exception:
        logger.exception("job run %s stopped outside a stage", run_id)
        _abort(
            db,
            run_id,
            ProcessingFailed(
                _STAGE_FOR_STATUS.get(run.status, "structure"),
                "unexpected_error",
                "Processing stopped unexpectedly.",
            ),
        )
        raise

    run.finished_at = datetime.now(timezone.utc)
    advance(run, STATUS_COMPLETED)
    db.commit()
    logger.info(
        "job.run.completed",
        extra={"job_run_id": run.id, "job_id": run.job_id},
    )
    return run


def _abort(db: Session, run_id: int, failure: ProcessingFailed) -> None:
    """Last resort: end a run that stopped for a reason we did not model.

    Takes an id rather than the object because the session may be poisoned —
    the rollback is what makes it usable again. Failing to record the failure
    is logged and swallowed; the original exception is what is worth
    propagating.
    """
    try:
        db.rollback()
        run = db.get(JobProcessingRun, run_id)
        if run is None or run.is_terminal:
            return
        fail_run(db, run, failure)
        db.commit()
    except Exception:
        logger.exception("could not record the terminal failure for job run %s", run_id)
        db.rollback()


def _run_structuring(db: Session, run: JobProcessingRun, job: Job, structurer: JobStructurer):
    with _stage(db, run, "structure"):
        try:
            result = structurer.structure(job)
        except ProviderError as exc:
            # The provider's own vocabulary is already this domain's vocabulary
            # — failures.py classifies these codes — so it travels unchanged
            # rather than being flattened into one "AI failed" code that
            # nothing could make a retry decision from. Mirrors
            # resume.pipeline._run_structuring.
            raise ProcessingFailed("structure", exc.code, exc.message) from exc
        except StructuringError as exc:
            raise ProcessingFailed("structure", exc.code, exc.message) from exc

    verify_job_provenance(result.candidate, job.description_text)
    return result


def _run_materialise(db: Session, run: JobProcessingRun, result) -> None:
    with _stage(db, run, "materialise"):
        db.add(
            JobRequirements(
                job_processing_run_id=run.id,
                structurer=result.structurer,
                provider=result.provider,
                model=result.model,
                schema_version=result.schema_version,
                prompt_version=result.prompt_version,
                provider_metadata=result.metadata or None,
                payload=result.candidate.model_dump(mode="json"),
            )
        )
        db.flush()
