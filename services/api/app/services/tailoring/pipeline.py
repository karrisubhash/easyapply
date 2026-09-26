"""Driving one tailoring run through the state machine.

The entry point is `process_run(db, *, run_id=...)`. That signature is the async
seam, exactly as it is in `app/services/resume/pipeline.py`: it takes an integer
and re-reads everything else, so nothing is carried in memory from the request
that started the work and moving the work to another process changes nothing
about this module.

Three stages, and the interesting one is the middle:

    generate   ask the configured tailor for a document, validate it against
               the schema. A generator's output is a proposal until this passes.

    validate   check that proposal against the profile version it claims to
               stand on. This stage CANNOT fail the run: a document that fails
               grounding is a document we successfully produced and will not
               vouch for, which is a completed run carrying a verdict, not a
               processing error. The distinction is the whole reason the status
               and the grounding status are two columns.

               For a document a MODEL wrote, one narrowing pass sits inside this
               stage: what grounding rejected is removed or restored, and the
               narrowed document is validated again from scratch. It is in this
               module rather than in the generator because deciding what happens
               to a failed proposal is the state machine's business, and it is
               skipped for the deterministic generator because a grounding error
               from that one is a bug we want to see. See `reconciliation.py`.

    render     lay the document out, read the file back (`rendering.verify`),
               and store the bytes. Reached only when grounding passed, which
               is what makes "an artifact exists" and "the document is
               trustworthy" the same fact; the read-back is what makes it the
               same fact about the FILE and not only about the JSON.

Error text is written by us, from a fixed vocabulary — never `str(exception)`.
A provider error quotes the prompt back, and the prompt contains the user's
entire career history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Job,
    JobProcessingRun,
    JobRequirements,
    MatchResult,
    Profile,
    ProfileVersion,
    Project,
)
from app.models.tailoring import (
    ALLOWED_TRANSITIONS,
    GENERATOR_AI,
    GROUNDING_FAILED,
    GROUNDING_PASSED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_GENERATING,
    STATUS_RENDERING,
    STATUS_VALIDATING,
    TailoredResume,
    TailoredResumeArtifact,
    TailoringIssue,
    TailoringRun,
)
from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.schemas.job import job_requirements_payload
from app.schemas.tailored_resume import TAILORED_RESUME_SCHEMA_VERSION
from app.services.storage import (
    StorageBackend,
    StorageError,
    build_artifact_key,
    content_digest,
    get_storage,
)
from app.services.tailoring import reconciliation
from app.services.tailoring.generation import (
    VERDICT_EVIDENCED,
    VERDICT_NOT_EVIDENCED,
    VERDICT_UNCERTAIN,
    GenerationError,
    TailoringInputs,
    get_tailor,
    requirement_labels,
)
from app.services.tailoring.providers import ProviderError
from app.services.tailoring.rendering import (
    DOCX_MEDIA_TYPE,
    RenderVerificationError,
    build_filename,
    render,
    verify,
)

logger = logging.getLogger(__name__)

#: Which stage a run was in when its status says so. Needed because
#: `failed_stage` has a CHECK constraint and "unknown" is not one of its values.
STAGE_FOR_STATUS = {
    STATUS_GENERATING: "generate",
    STATUS_VALIDATING: "validate",
    STATUS_RENDERING: "render",
}

_MAX_ERROR_MESSAGE = 400


class TailoringFailed(Exception):
    """A stage failed. Carries where and why, in our own words."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message[:_MAX_ERROR_MESSAGE]


class IllegalTransition(RuntimeError):
    pass


def advance(run: TailoringRun, status: str) -> None:
    """Move a run to its next state, refusing anything the graph forbids."""
    if status not in ALLOWED_TRANSITIONS.get(run.status, frozenset()):
        raise IllegalTransition(f"{run.status} -> {status} is not a legal transition")
    run.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_run(db: Session, run_id: int) -> TailoringRun | None:
    return db.get(TailoringRun, run_id)


def _snapshot_loaders():
    """Load the whole snapshot in one go: tailoring reads every child table."""
    return (
        selectinload(ProfileVersion.experiences),
        selectinload(ProfileVersion.education),
        selectinload(ProfileVersion.skills),
        selectinload(ProfileVersion.projects).selectinload(Project.technologies),
        selectinload(ProfileVersion.certifications),
        # The contact header's links. Omitted here, `version.links` lazy-loads
        # inside the generator — or, once the session has moved on, does not
        # load at all and the resume goes out with no links on it.
        selectinload(ProfileVersion.links),
    )


def _emphasis(db: Session, run: TailoringRun) -> tuple[str, ...]:
    """Requirements a prior match could not evidence.

    A hint about ORDER, never a licence to claim anything: the generator may
    lead with true content that speaks to these, and grounding does not relax by
    one word because a requirement was emphasised. Empty when no match was
    supplied, which is the ordinary case for tailoring without matching first.
    """
    if run.match_result_id is None:
        return ()
    match = db.get(MatchResult, run.match_result_id)
    if match is None:
        return ()
    return tuple(
        result.requirement_label
        for result in match.requirement_results
        if result.is_required and result.status != STATUS_MATCHED
    )


#: A match's verdict, in the words `<requirement_support>` gives a model.
_SUPPORT_FOR_STATUS = {
    STATUS_MATCHED: VERDICT_EVIDENCED,
    STATUS_UNCERTAIN: VERDICT_UNCERTAIN,
    STATUS_MISSING: VERDICT_NOT_EVIDENCED,
}


def _requirement_verdicts(db: Session, run: TailoringRun) -> tuple[tuple[str, str], ...]:
    """Every requirement the match judged, with its verdict, for the model.

    `_emphasis` is kept exactly as it was, because the deterministic generator
    reads it and its output must not move. This is the fuller reading a model
    needs to be told what it must NOT claim as well as what to lead with —
    required and preferred alike, since a preferred requirement the profile
    does not evidence is no more claimable than a required one.
    """
    if run.match_result_id is None:
        return ()
    match = db.get(MatchResult, run.match_result_id)
    if match is None:
        return ()
    return tuple(
        (result.requirement_label, _SUPPORT_FOR_STATUS[result.status])
        for result in match.requirement_results
        if result.status in _SUPPORT_FOR_STATUS
    )


def process_run(
    db: Session, *, run_id: int, storage: StorageBackend | None = None
) -> TailoringRun | None:
    """Take one run from queued to terminal.

    Returns the run, or None when the id names nothing — a run deleted between
    being enqueued and being picked up is not an error worth raising into a
    worker thread.
    """
    run = _load_run(db, run_id)
    if run is None:
        logger.warning("tailoring.run.missing", extra={"tailoring_run_id": run_id})
        return None

    if run.is_terminal:
        # A duplicate delivery, or a late one for a run the service abandoned
        # when the user asked again. Harmless and expected: the queue is
        # advisory, and a terminal run is the record.
        logger.info(
            "tailoring.run.already_terminal",
            extra={"tailoring_run_id": run.id, "status": run.status},
        )
        return run

    run.started_at = run.started_at or _now()
    db.flush()

    # What the generator reported about itself, kept outside the transaction:
    # a failure after a model answered — a render that raised, a store that
    # refused — rolls back every write, and the run should still say which
    # model wrote the document it could not keep, and what that call cost.
    provenance: dict = {}
    try:
        _run_stages(db, run, storage or get_storage(), provenance)
    except TailoringFailed as failure:
        db.rollback()
        _fail(db, run_id, failure, provenance)
        return _load_run(db, run_id)
    except Exception:
        # Nothing above this catches. A stage raising something unmodelled must
        # still leave a terminal run behind, or the UI shows a spinner forever.
        logger.exception("tailoring run %s raised", run_id)
        # The stage is read BEFORE the rollback. Nothing is committed until the
        # run is terminal, so after a rollback the row says `queued` again and
        # every unexpected failure was recorded as a "generate" failure — a
        # render that raised told the user their resume could not be written.
        stage = _stage_of(run)
        db.rollback()
        _fail(
            db,
            run_id,
            TailoringFailed(
                stage,
                "unexpected_error",
                "Something went wrong while generating this resume.",
            ),
            provenance,
        )
        return _load_run(db, run_id)

    return run


def _mismatch(
    db: Session,
    run: TailoringRun,
    *,
    version: ProfileVersion,
    job: Job,
    requirements_row: JobRequirements,
) -> str | None:
    """Which of the run's inputs does not belong to it, or None when all do.

    The worker loads every input by primary key, because the service resolved
    ownership when it opened the run. That makes the run row the only thing
    standing between a worker and another user's career: a row written by
    anything other than `start_tailoring` — a script, a migration, a bug — and
    naming someone else's version would be tailored, and the file stored under
    this run's user. So the links are re-checked here, where the data is used,
    rather than trusted from where it was written:

      * the version belongs to the run's user (through its profile),
      * so does the job,
      * the requirements row is a reading of THIS job,
      * and a match, when one is named, is the user's, for this job, this
        version and this requirements row — the same rule
        `service._load_match` applies on the way in.
    """
    profile = db.get(Profile, version.profile_id)
    if profile is None or profile.user_id != run.user_id:
        return "profile_version_owner"
    if job.user_id != run.user_id:
        return "job_owner"
    processing_run = db.get(JobProcessingRun, requirements_row.job_processing_run_id)
    if processing_run is None or processing_run.job_id != run.job_id:
        return "requirements_job"
    if run.match_result_id is not None:
        match = db.get(MatchResult, run.match_result_id)
        if match is not None and (
            match.user_id != run.user_id
            or match.job_id != run.job_id
            or match.profile_version_id != run.profile_version_id
            or match.job_requirements_id != run.job_requirements_id
        ):
            return "match"
    return None


def _stage_of(run: TailoringRun) -> str:
    """The stage the in-memory run had reached, without touching the database.

    Read from the instance's own loaded state: the session may be mid-failure
    (a flush that raised), and an attribute load now would raise again.
    """
    status = inspect(run).dict.get("status")
    return STAGE_FOR_STATUS.get(status, "generate")


def _run_stages(
    db: Session, run: TailoringRun, storage: StorageBackend, provenance: dict
) -> None:
    # Re-read rather than trusting anything carried in from the request: this
    # function's whole contract is that it takes an integer and re-derives the
    # rest, which is what lets it run in another process.
    version = db.get(
        ProfileVersion, run.profile_version_id, options=_snapshot_loaders()
    )
    if version is None:
        raise TailoringFailed(
            "generate",
            "empty_profile_version",
            "The profile version this resume was to be built from is no longer available.",
        )

    job = db.get(Job, run.job_id)
    requirements_row = db.get(JobRequirements, run.job_requirements_id)
    if job is None or requirements_row is None:
        raise TailoringFailed(
            "generate",
            "generation_failed",
            "The job this resume was to be aimed at is no longer available.",
        )

    mismatch = _mismatch(db, run, version=version, job=job, requirements_row=requirements_row)
    if mismatch is not None:
        # Ids and the name of the broken link only. Which user's data a row
        # points at is not something to put in a message a user can read.
        logger.error(
            "tailoring.run.inconsistent",
            extra={"tailoring_run_id": run.id, "mismatch": mismatch},
        )
        raise TailoringFailed(
            "generate",
            "unexpected_error",
            "Something went wrong while generating this resume.",
        )

    # After the ownership checks, so nothing about a version that is not this
    # run's — not even that it is empty — decides what happens to the run.
    has_content = bool(
        version.experiences or version.projects or version.education or version.certifications
    )
    if not has_content:
        raise TailoringFailed(
            "generate",
            "empty_profile_version",
            "This profile version has no experience, projects, education or certifications to build a resume from.",
        )

    requirements = job_requirements_payload(requirements_row.payload)

    # --- generate --------------------------------------------------------------
    advance(run, STATUS_GENERATING)
    db.flush()

    # The run's own column, not the flag. The row records which generator it is
    # for at the moment it is created; re-reading configuration here would let a
    # flag flipped between enqueue and execution produce a run whose `generator`
    # column disagrees with what actually wrote the document.
    tailor = get_tailor(run.generator)
    try:
        generated = tailor.generate(
            TailoringInputs(
                version=version,
                job=job,
                requirements=requirements,
                emphasise=_emphasis(db, run),
                requirement_verdicts=_requirement_verdicts(db, run),
            )
        )
    except ProviderError as exc:
        raise TailoringFailed("generate", exc.code, _provider_message(exc.code)) from exc
    except ValidationError as exc:
        # The generator produced something that is not the document schema.
        raise TailoringFailed(
            "generate",
            "document_schema_invalid",
            "The generated resume did not have the expected structure.",
        ) from exc
    except GenerationError as exc:
        raise TailoringFailed("generate", exc.code, exc.message) from exc

    # `generator` is NOT written back from the result. The row named the
    # generator before the work started and `get_tailor` obeyed it, so copying
    # the generator's own self-description over that column could only ever
    # introduce a disagreement — and the column is what a reader consults six
    # months later to find out how a document was produced. The model and the
    # prompt version are a different matter: the row cannot know either until
    # the call has been made. Neither can the provider or what the call cost,
    # which are stored the way the job and resume pipelines store theirs.
    provenance.update(
        model=generated.model,
        prompt_version=generated.prompt_version,
        provider=generated.provider,
        provider_metadata=_provider_metadata(generated.metadata),
    )
    for column, value in provenance.items():
        setattr(run, column, value)

    # --- validate --------------------------------------------------------------
    #
    # Cannot fail the run. See the module docstring.
    advance(run, STATUS_VALIDATING)
    db.flush()

    labels = frozenset(requirement_labels(requirements))

    # Validate, repair if this is a model's document, validate again. The whole
    # sequence belongs to `reconciliation.settle` so that the evaluation harness
    # measures the same one that runs here.
    #
    # `repair_allowed` is the run's own generator column — the same one
    # `get_tailor` was handed above, so the stage reads off a single record of
    # what this run is. The asymmetry is deliberate: `DeterministicTailor` copies
    # its clauses and its identity fields out of the snapshot, so a grounding
    # error from it is a defect in our own selection code and repairing it would
    # be hiding a bug from ourselves. A model failing on two lines out of thirty
    # is not a defect, it is what a model does, and withholding the other
    # twenty-eight over it serves nobody.
    settlement = reconciliation.settle(
        generated.document,
        version,
        job_requirement_labels=labels,
        repair_allowed=run.generator == GENERATOR_AI,
    )
    document = settlement.document
    report = settlement.report
    repairs = settlement.repairs

    if settlement.errors_before:
        logger.info(
            "tailoring.validated",
            extra={
                "tailoring_run_id": run.id,
                "errors_before": settlement.errors_before,
                "repairs": len(repairs),
                "errors_remaining": len(report.errors),
                "rescued": settlement.repaired,
            },
        )

    tailored = TailoredResume(
        tailoring_run_id=run.id,
        payload=document.model_dump(mode="json"),
        schema_version=TAILORED_RESUME_SCHEMA_VERSION,
        grounding_status=GROUNDING_PASSED if report.passed else GROUNDING_FAILED,
        error_count=len(report.errors),
        # The repairs are counted here rather than anywhere quieter. A document
        # six lines shorter than the model wrote is a document the user should be
        # told about, in the same list and the same shape as everything else we
        # found wrong with it.
        warning_count=len(report.warnings) + len(repairs),
    )
    db.add(tailored)
    db.flush()

    for finding in settlement.findings:
        db.add(
            TailoringIssue(
                tailored_resume_id=tailored.id,
                severity=finding.severity,
                section=finding.section,
                field_path=finding.field_path,
                claim=finding.claim,
                reason=finding.reason,
            )
        )
    db.flush()

    if not report.passed:
        # A finished run with a verdict, not a failure. `finished_at` is set and
        # `failed_stage` is not, which is exactly what the CHECK constraints
        # require of a completed run.
        advance(run, STATUS_COMPLETED)
        run.finished_at = _now()
        db.commit()
        logger.info(
            "tailoring.completed",
            extra={
                "tailoring_run_id": run.id,
                "grounding_status": GROUNDING_FAILED,
                "error_count": tailored.error_count,
                "warning_count": tailored.warning_count,
                **_provenance_log(run),
            },
        )
        return

    # --- render ----------------------------------------------------------------
    advance(run, STATUS_RENDERING)
    db.flush()

    try:
        # The stored document, not the generated one: after a repair they are
        # different, and the file must be the thing that was validated.
        data = render(document)
        # Then read the FILE back, as its recipient's tooling will. Grounding
        # approved the document; nothing had ever checked that the bytes say
        # it — that they open, carry the candidate's name, hold no blank
        # paragraph and link only to external urls. A file that fails is not
        # stored, so an artifact still means "this is the approved document".
        verify(data, document)
    except RenderVerificationError as exc:
        logger.warning(
            "tailoring.render.rejected",
            extra={"tailoring_run_id": run.id, "reason": exc.reason},
        )
        raise TailoringFailed(
            "render", "render_failed", "The resume could not be written to a file."
        ) from exc
    except Exception as exc:
        raise TailoringFailed(
            "render", "render_failed", "The resume could not be written to a file."
        ) from exc

    digest = content_digest(data)
    key = build_artifact_key(
        user_id=run.user_id, content_sha256=digest, extension="docx"
    )
    try:
        storage.put(key, data, content_type=DOCX_MEDIA_TYPE)
    except StorageError as exc:
        raise TailoringFailed(
            "render",
            "artifact_storage_failed",
            "The resume was generated but could not be saved. Try again.",
        ) from exc

    db.add(
        TailoredResumeArtifact(
            tailored_resume_id=tailored.id,
            storage_key=key,
            filename=build_filename(
                job_title=job.job_title,
                company_name=job.company_name,
                version_number=version.version_number,
            ),
            media_type=DOCX_MEDIA_TYPE,
            byte_size=len(data),
            content_sha256=digest,
        )
    )

    advance(run, STATUS_COMPLETED)
    run.finished_at = _now()
    db.commit()

    logger.info(
        "tailoring.completed",
        extra={
            "tailoring_run_id": run.id,
            "grounding_status": GROUNDING_PASSED,
            "warning_count": tailored.warning_count,
            "byte_size": len(data),
            **_provenance_log(run),
        },
    )


#: The longest string `provider_metadata` keeps. A response id, a model name or
#: a stop reason fits many times over; a value longer than this is not an
#: identifier, and the one thing this column must never become is a place a
#: completion's text lands because an adapter put it somewhere unexpected.
_MAX_METADATA_STRING = 128


def _provider_metadata(metadata: dict | None) -> dict | None:
    """What of a provider's metadata may be stored: flat counts, ids, timings.

    The adapters already promise this (`ai/providers/base.py`); it is enforced
    again here, at the point it is written, because this column is read back by
    anyone auditing a run and the promise is made in a different module. Only
    scalar values survive, strings only when short. None when nothing does, so
    a deterministic run's column stays NULL rather than `{}` — the same
    `metadata or None` the job and resume pipelines write.
    """
    kept = {
        str(key)[:64]: value
        for key, value in (metadata or {}).items()
        if isinstance(value, (bool, int, float))
        or (isinstance(value, str) and len(value) <= _MAX_METADATA_STRING)
    }
    return kept or None


def _provenance_log(run: TailoringRun) -> dict:
    """The completion log's view of what answered: provider, model and cost."""
    return {
        "generator": run.generator,
        "ai_provider": run.provider,
        "ai_model": run.model,
        "prompt_version": run.prompt_version,
        "provider_metadata": run.provider_metadata,
    }


def _provider_message(code: str) -> str:
    """What to tell the user about a provider failure.

    Never the provider's own text: it quotes the prompt, and the prompt is the
    user's career history. One sentence per class of failure, and every one of
    them says what the user can do rather than what the vendor did.
    """
    if code in {"ai_rate_limited", "ai_provider_unavailable", "ai_timeout", "ai_provider_error"}:
        return "The writing service was busy. You can try again in a moment."
    if code in {
        "ai_not_configured",
        "ai_unauthorized",
        "ai_authentication_failed",
        "ai_billing_blocked",
    }:
        return "Resume generation is not configured on this deployment."
    if code == "ai_content_rejected":
        return "The writing service declined to generate from this content."
    return "The generated resume could not be produced."


def _fail(
    db: Session, run_id: int, failure: TailoringFailed, provenance: dict | None = None
) -> None:
    """Write a terminal failure on a session that may have just been rolled back.

    Re-reads the run rather than reusing the object the caller held: after a
    rollback that instance is detached from anything the database believes, and
    writing through it would either raise or silently do nothing.

    `provenance` is what the generator reported before the failure — model,
    prompt version, provider and its metadata — written back because the
    rollback took it with everything else.
    """
    run = _load_run(db, run_id)
    if run is None or run.is_terminal:
        return

    for column, value in (provenance or {}).items():
        setattr(run, column, value)
    run.status = STATUS_FAILED
    run.failed_stage = failure.stage
    run.error_code = failure.code
    run.error_message = failure.message
    run.finished_at = _now()
    db.commit()

    logger.warning(
        "tailoring.failed",
        extra={
            "tailoring_run_id": run_id,
            "stage": failure.stage,
            "error_code": failure.code,
        },
    )
