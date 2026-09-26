"""The tailoring application service: resolve, authorize, start, read.

Everything that makes a tailored resume a trustworthy RECORD rather than a
document that appeared lives here — which profile version may be tailored,
which requirements row is pinned, who may read the result, and who may download
the file. The generation itself is `pipeline.process_run`, called by a worker
and never by this module.

The rules this layer enforces that nothing below it can:

  * **A draft is never tailored.** The same rule matching applies, for a
    stronger reason: a tailored resume is checked against the snapshot it was
    generated from, and a snapshot that can still change would make a stored
    "grounded" verdict meaningless the moment the user edited a bullet.

  * **Ownership is resolved from the profile side, every time.** A
    `profile_version_id` is a global primary key and proves nothing; it is
    loaded through a join to `profiles.user_id`, and a version belonging to
    someone else is a 404. Same for a job, a match and a run.

  * **The requirements row is pinned, not the job.** Reprocessing a job writes a
    new requirements row, so pinning the row is what makes it possible to say
    six months later what the document was actually written against.

  * **An artifact is only reachable through its run.** There is no endpoint
    that takes a storage key, and no schema that exposes one. The only way to
    the bytes is `get_artifact`, which starts from a run id scoped to the
    caller — so changing a number in a URL reaches a 404, not a stranger's
    resume.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import (
    Job,
    JobProcessingRun,
    JobRequirements,
    MatchResult,
    Profile,
    ProfileVersion,
)
from app.models.profile_version import STATUS_CONFIRMED
from app.models.tailoring import (
    GENERATOR_AI,
    GENERATOR_DETERMINISTIC,
    GROUNDING_PASSED,
    STATUS_FAILED,
    TERMINAL_STATUSES,
    TailoredResume,
    TailoringRun,
)
from app.services.errors import ConflictError, NotFoundError
from app.services.job import service as job_service
from app.services.storage import ObjectNotFound, StorageBackend, get_storage
from app.services.tailoring.failures import (
    ABANDONED_ERROR_CODE,
    ABANDONED_ERROR_MESSAGE,
    is_retryable,
)
from app.services.tailoring.pipeline import STAGE_FOR_STATUS

logger = logging.getLogger(__name__)


# --- resolving the inputs -------------------------------------------------------


def _load_profile_version(db: Session, *, user_id: int, profile_version_id: int) -> ProfileVersion:
    """A version by its global id, scoped to its owner.

    The join to `profiles` is the authorization check. Without it any primary
    key in the table would be accepted — the classic insecure direct object
    reference, and the one this domain would leak a whole career history
    through.
    """
    version = db.scalar(
        select(ProfileVersion)
        .join(Profile, Profile.id == ProfileVersion.profile_id)
        .where(ProfileVersion.id == profile_version_id, Profile.user_id == user_id)
    )
    if version is None:
        raise NotFoundError("That profile version does not exist.")
    return version


def _load_current_version(db: Session, *, user_id: int) -> ProfileVersion:
    version = db.scalar(
        select(ProfileVersion)
        .join(Profile, Profile.id == ProfileVersion.profile_id)
        .where(Profile.user_id == user_id, ProfileVersion.is_current.is_(True))
    )
    if version is None:
        raise ConflictError(
            "This profile has no confirmed version yet. Confirm a version before tailoring."
        )
    return version


def _load_requirements(db: Session, *, job: Job) -> JobRequirements:
    """The most recent completed structuring of this job."""
    requirements = db.scalar(
        select(JobRequirements)
        .join(JobProcessingRun, JobProcessingRun.id == JobRequirements.job_processing_run_id)
        .where(JobProcessingRun.job_id == job.id)
        .order_by(JobProcessingRun.attempt.desc())
        .limit(1)
    )
    if requirements is None:
        raise ConflictError(
            "This job has not been structured yet, so there is nothing to tailor against."
        )
    return requirements


def _load_match(
    db: Session,
    *,
    user_id: int,
    match_id: int,
    job_id: int,
    profile_version_id: int,
    job_requirements_id: int,
) -> MatchResult:
    """The caller's match, and only if it is about exactly these inputs.

    A match is a verdict on ONE profile version against ONE reading of the
    posting, and the run uses its per-requirement verdicts to decide what the
    document leads with — and, for a model, what it may and may not claim. The
    job alone used to be checked, so a match computed for version 2 could steer
    a resume built from version 3, or a match against last week's structuring
    of the posting could steer one aimed at today's: verdicts about a snapshot
    and a requirement list the document is not built from.

    Each mismatch is a 409 rather than a 404: the match exists and is the
    caller's own. It is simply about something else, which is a request that
    does not make sense rather than one that is not allowed.
    """
    match = db.scalar(
        select(MatchResult)
        .where(MatchResult.id == match_id, MatchResult.user_id == user_id)
        .options(selectinload(MatchResult.requirement_results))
    )
    if match is None:
        raise NotFoundError("That match does not exist.")
    if match.job_id != job_id:
        raise ConflictError("That match is for a different job.")
    if match.profile_version_id != profile_version_id:
        raise ConflictError(
            "That match was made against a different profile version. "
            "Match this version against the job first, or tailor without the match."
        )
    if match.job_requirements_id != job_requirements_id:
        raise ConflictError(
            "That match was made against an earlier reading of this job. "
            "Match again, then tailor."
        )
    return match


# --- starting -------------------------------------------------------------------


def _rollout_user_ids() -> frozenset[int]:
    """The narrow rollout list, parsed once per call from configuration.

    Anything that is not an integer is dropped rather than raised on. This value
    is read while starting a run, and a typo in a deployment variable must not
    turn "tailor my resume" into a 500 — the cost of ignoring a malformed entry
    is that a canary user quietly gets the deterministic generator, which is the
    behaviour everyone else already has.
    """
    out: set[int] = set()
    for part in settings.TAILORING_AI_USER_IDS.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out)


def select_generator(user_id: int) -> str:
    """Which generator a new run is for.

    The whole of this domain's rollout policy, in one function, called once per
    run and recorded on the row. `generation.get_tailor` then takes that recorded
    value rather than consulting configuration again, so a run cannot be written
    by a generator other than the one it says it was.

    Two ways to reach the model, and the order matters: the global flag first,
    because when it is on the list is irrelevant, then the narrow list.
    """
    if settings.TAILORING_AI_ENABLED:
        return GENERATOR_AI
    if user_id in _rollout_user_ids():
        return GENERATOR_AI
    return GENERATOR_DETERMINISTIC


def _next_attempt(db: Session, *, job_id: int, profile_version_id: int) -> int:
    highest = db.scalar(
        select(TailoringRun.attempt)
        .where(
            TailoringRun.job_id == job_id,
            TailoringRun.profile_version_id == profile_version_id,
        )
        .order_by(TailoringRun.attempt.desc())
        .limit(1)
    )
    return (highest or 0) + 1


#: The unique constraint two simultaneous starts can collide on.
_ATTEMPT_CONSTRAINT = "uq_tailoring_runs_job_version_attempt"

#: How many times a start re-reads and retries after losing that race to a run
#: that had already finished. Bounded so a bug cannot loop a request forever.
_ATTEMPT_RACE_RETRIES = 3


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint name PostgreSQL reported, when the driver exposes it."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def _in_flight(db: Session, *, job_id: int, profile_version_id: int) -> TailoringRun | None:
    """A run for these inputs that has not finished.

    Returned rather than refused so that pressing Generate twice gives the user
    the run that is already going, instead of a second one racing it for the
    same answer.

    Non-terminal is necessary for "in flight", not sufficient: a run can be
    non-terminal forever because the thing that would have finished it is gone.
    `start_tailoring` asks `_abandon_if_unattended` about a stalled one before
    treating it as going.
    """
    return db.scalar(
        select(TailoringRun)
        .where(
            TailoringRun.job_id == job_id,
            TailoringRun.profile_version_id == profile_version_id,
            TailoringRun.status.not_in(tuple(TERMINAL_STATUSES)),
        )
        .order_by(TailoringRun.id.desc())
        .limit(1)
    )


def _abandon_if_unattended(db: Session, run: TailoringRun) -> bool:
    """End a stalled run that nothing is working on. True when it is not in flight.

    The defect this closes: `_in_flight` returned ANY non-terminal run, so a run
    whose worker had gone — the in-process queue lost on an API restart, a
    process killed mid-generation — was handed back to every later press of
    Generate as "already going", with `created=False` and nothing re-enqueued.
    The UI offered "Generate again" beside a button that could never do it, for
    that job and version, permanently.

    "Stalled" alone is not enough to take a run away, because it is a clock
    reading, not an observation: a run that sat in a saturated pool before its
    worker reached it looks exactly as old as one whose queue was lost. What
    tells them apart is the row lock. `pipeline.process_run` writes `started_at`
    before its first stage and commits only when the run is terminal, so a live
    worker holds this row's lock for the whole of its work — including the
    provider call — and a dead one does not, because a closed connection's
    transaction is rolled back and its locks go with it. `SKIP LOCKED` asks
    exactly that question without waiting on the answer: a request thread must
    not block for minutes behind a model call.

    Taking the lock ourselves is also what makes the write safe. Once it is
    held, no worker can start this run until we commit, and a worker that
    reaches it afterwards finds it terminal and leaves it alone
    (`process_run`'s already-terminal branch).

    The run is FAILED, never reset: attempts are append-only, so "attempt 1
    abandoned, attempt 2 completed" is what the table says afterwards — the same
    posture the resume reaper takes.
    """
    claimed = db.scalar(
        select(TailoringRun.id)
        .where(
            TailoringRun.id == run.id,
            TailoringRun.status.not_in(tuple(TERMINAL_STATUSES)),
        )
        .with_for_update(skip_locked=True)
    )
    db.refresh(run)
    if claimed is None:
        # Locked by a worker that is alive — in flight after all — or finished
        # between the two reads, in which case it is simply not in flight.
        return run.is_terminal

    # A queued run never reached a stage; "generate" is the one it was waiting
    # to enter, and `failed_stage` has no value for "before any stage".
    stage = STAGE_FOR_STATUS.get(run.status, "generate")
    run.status = STATUS_FAILED
    run.failed_stage = stage
    run.error_code = ABANDONED_ERROR_CODE
    run.error_message = ABANDONED_ERROR_MESSAGE
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    logger.warning(
        "tailoring.abandoned",
        extra={"tailoring_run_id": run.id, "stage": stage, "error_code": ABANDONED_ERROR_CODE},
    )
    return True


def start_tailoring(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    profile_version_id: int | None = None,
    match_result_id: int | None = None,
    queue=None,
) -> tuple[TailoringRun, bool]:
    """Open a tailoring run and hand it to a worker.

    Returns `(run, created)`. `created` is False when a run for the same job and
    version is already in flight — which makes a double-click, or a client
    retrying after a timeout, resolve to the run that already exists rather than
    starting a second generation. That holds for two presses arriving at the
    same instant too: the one that loses the attempt-number race returns the
    winner's run (see the savepoint below).

    A stalled run nothing is working on is NOT in flight. It is failed as
    `worker_abandoned` and a new attempt opens beside it; see
    `_abandon_if_unattended` for how "nothing is working on it" is decided.

    Deliberately NOT idempotent beyond that. Two completed runs for the same
    inputs are legitimate: a tailored resume is a draft, and asking for another
    one is a reasonable thing to want. Matching takes the opposite position for
    the opposite reason — see `matching.service.create_match`.
    """
    job = job_service.get_job(db, user_id=user_id, job_id=job_id)
    requirements = _load_requirements(db, job=job)

    if profile_version_id is None:
        version = _load_current_version(db, user_id=user_id)
    else:
        version = _load_profile_version(
            db, user_id=user_id, profile_version_id=profile_version_id
        )

    if version.status != STATUS_CONFIRMED:
        raise ConflictError(
            "Only a confirmed profile version can be tailored. A draft can still change, "
            "which would make the grounding check meaningless."
        )

    # Before the in-flight check, so a request naming a match that does not fit
    # is refused as such rather than answered with whatever run is going.
    match = None
    if match_result_id is not None:
        match = _load_match(
            db,
            user_id=user_id,
            match_id=match_result_id,
            job_id=job.id,
            profile_version_id=version.id,
            job_requirements_id=requirements.id,
        )

    existing = _in_flight(db, job_id=job.id, profile_version_id=version.id)
    if existing is not None and not (
        is_stalled(existing) and _abandon_if_unattended(db, existing)
    ):
        return existing, False

    # Two presses of Generate that arrive together both find nothing in flight
    # and both compute the same next attempt; the second INSERT then violates
    # `uq_tailoring_runs_job_version_attempt`. That used to surface as a 500.
    # The constraint is the arbiter — the same posture matching takes — so the
    # loser's INSERT runs in a savepoint, and on that one violation it reads the
    # winner's run and returns it, exactly as a press that arrived a moment
    # later would have been answered. If the winner is already terminal the
    # attempt number is simply recomputed.
    for _ in range(_ATTEMPT_RACE_RETRIES):
        run = TailoringRun(
            user_id=user_id,
            job_id=job.id,
            profile_version_id=version.id,
            job_requirements_id=requirements.id,
            match_result_id=match.id if match else None,
            attempt=_next_attempt(db, job_id=job.id, profile_version_id=version.id),
            # Recorded before the work starts, and then OBEYED: the pipeline
            # reads this column to choose a tailor rather than re-reading
            # configuration, so a flag flipped between enqueue and execution
            # cannot produce a run whose provenance disagrees with its document.
            # It also means a run that fails during generation still says which
            # generator failed.
            generator=select_generator(user_id),
        )
        try:
            with db.begin_nested():
                db.add(run)
                db.flush()
        except IntegrityError as exc:
            if _violated_constraint(exc) != _ATTEMPT_CONSTRAINT:
                raise
            winner = _in_flight(db, job_id=job.id, profile_version_id=version.id)
            if winner is not None:
                return winner, False
            continue
        break
    else:
        raise ConflictError(
            "Another request for this resume is being started. Try again in a moment."
        )

    db.commit()
    db.refresh(run)

    logger.info(
        "tailoring.started",
        extra={
            "tailoring_run_id": run.id,
            "job_id": job.id,
            "profile_version_id": version.id,
            "attempt": run.attempt,
        },
    )

    if queue is not None:
        # After the commit, never before: a worker that picks the id up while
        # the row is still uncommitted finds nothing there.
        queue.enqueue(run.id)

    return run, True


# --- reading ----------------------------------------------------------------------


def _detail_loaders():
    return (
        selectinload(TailoringRun.result).selectinload(TailoredResume.issues),
        selectinload(TailoringRun.result).selectinload(TailoredResume.artifact),
    )


def get_run(db: Session, *, user_id: int, run_id: int) -> TailoringRun:
    """One tailoring run, scoped to its owner. A stranger's id is a 404."""
    run = db.scalar(
        select(TailoringRun)
        .where(TailoringRun.id == run_id, TailoringRun.user_id == user_id)
        .options(*_detail_loaders())
    )
    if run is None:
        raise NotFoundError("That tailored resume does not exist.")
    return run


def list_runs(db: Session, *, user_id: int, job_id: int | None = None) -> list[TailoringRun]:
    """This caller's tailoring runs, newest first.

    When `job_id` is given it goes through the job service first, so a job
    belonging to someone else is a 404 before any run row is read — rather than
    an empty list that quietly confirms the job exists.
    """
    query = select(TailoringRun).where(TailoringRun.user_id == user_id)

    if job_id is not None:
        job = job_service.get_job(db, user_id=user_id, job_id=job_id)
        query = query.where(TailoringRun.job_id == job.id)

    return list(
        db.scalars(
            query.order_by(TailoringRun.created_at.desc(), TailoringRun.id.desc()).options(
                *_detail_loaders()
            )
        )
    )


def is_stalled(run: TailoringRun, *, now: datetime | None = None) -> bool:
    """Whether a non-terminal run has stopped showing signs of life.

    A read-side judgement, not a state. Nothing writes "stalled" to the
    database, because the run might still be running — the worker could be
    waiting on a slow provider — and recording a guess as a fact is how a
    working run gets abandoned. What this changes is what the UI says and
    whether a retry is offered.

    The one write it can lead to is made only after the guess has been checked:
    when the user asks for the same document again, `_abandon_if_unattended`
    fails a stalled run only if no worker holds its row.
    """
    if run.is_terminal:
        return False
    moment = now or datetime.now(timezone.utc)
    started = run.started_at or run.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return moment - started > timedelta(seconds=settings.TAILORING_STALE_AFTER_SECONDS)


def can_retry(run: TailoringRun, *, now: datetime | None = None) -> bool:
    """Whether offering the user a "try again" button would be honest.

    True for a transient failure and for a run that has stalled. False while a
    run is genuinely in flight, false for a permanent failure the same inputs
    will reproduce, and false for a COMPLETED run — including one whose document
    failed grounding. That last case is the important one: re-rolling a
    generation until the validator happens to pass is not a retry, it is
    laundering. Generating again is offered as a fresh action with its own
    button, not as a retry of a verdict.
    """
    if run.status in TERMINAL_STATUSES:
        return run.status == "failed" and is_retryable(run.error_code)
    return is_stalled(run, now=now)


# --- the artifact ------------------------------------------------------------------


class ArtifactUnavailable(ConflictError):
    """The run exists and there is no file to give you.

    A distinct error from "not found" because the two mean different things to
    the user: one is a wrong link, the other is a document that exists and is
    not one we will hand over. Both are answered from a run the caller already
    owns, so neither reveals anything.
    """


def get_artifact(
    db: Session, *, user_id: int, run_id: int, storage: StorageBackend | None = None
) -> tuple[bytes, str, str]:
    """The rendered file, as `(data, filename, media_type)`.

    Every gate in one place, in the order they matter:

      1. The run must be the caller's. Resolved by `get_run`; anyone else's id
         is a 404 before a single byte is considered.
      2. The run must have completed and produced a document.
      3. The document must have PASSED grounding. A failed one has no artifact
         row by construction, and this check is the second lock on the same
         door — §9 says an invalid artifact must not be downloadable, and a
         requirement that important should not rest on one mechanism.
      4. The bytes must actually be there. A storage key whose object has gone
         is reported as unavailable rather than as a 500: nothing the user did
         is wrong, and regenerating is the fix.
    """
    run = get_run(db, user_id=user_id, run_id=run_id)

    tailored = run.result
    if tailored is None:
        raise ArtifactUnavailable(
            "This resume has not finished generating yet."
            if not run.is_terminal
            else "This attempt did not produce a resume."
        )

    if tailored.grounding_status != GROUNDING_PASSED:
        raise ArtifactUnavailable(
            "This resume contains statements that could not be traced back to your profile, "
            "so it was not approved for download."
        )

    artifact = tailored.artifact
    if artifact is None:
        raise ArtifactUnavailable("This resume has no downloadable file.")

    backend = storage or get_storage()
    try:
        data = backend.get(artifact.storage_key)
    except ObjectNotFound as exc:
        raise ArtifactUnavailable(
            "The generated file is no longer available. Generate the resume again."
        ) from exc

    return data, artifact.filename, artifact.media_type
