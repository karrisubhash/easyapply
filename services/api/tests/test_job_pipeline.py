"""The processing state machine, against a real database.

Processing runs synchronously today — see `app/services/job/pipeline.py`'s
module docstring — so every test here calls `pipeline.process_run` directly,
the same way the request handler does, rather than draining a queue.
"""

import pytest

from app.models import Job, JobProcessingEvent, JobProcessingRun, JobRequirements
from app.models.job import ALLOWED_TRANSITIONS, STATUS_COMPLETED, STATUS_FAILED, STATUS_QUEUED
from app.services.job import pipeline
from app.services.job.providers import ProviderError
from app.services.job.structuring import JobStructuringResult, StructuringError
from app.schemas.job_candidate import JobCandidate


def make_job(db, user, **overrides):
    defaults = dict(
        user_id=user.id,
        source_type="linkedin",
        source_url="https://x.example/1",
        canonical_url="https://x.example/1",
        content_hash="a" * 64,
        job_title="Engineer",
        raw_payload={},
        description_text="Requirements:\n- Python\n",
    )
    defaults.update(overrides)
    job = Job(**defaults)
    db.add(job)
    db.flush()
    return job


def make_run(db, job, attempt=1):
    run = JobProcessingRun(job_id=job.id, attempt=attempt)
    db.add(run)
    db.commit()
    return run


class FakeStructurer:
    name = "fake"
    model = None

    def __init__(self, candidate=None, error=None):
        self._candidate = candidate if candidate is not None else JobCandidate()
        self._error = error

    def structure(self, job):
        if self._error:
            raise self._error
        return JobStructuringResult(candidate=self._candidate, structurer=self.name)


# --- the state graph itself --------------------------------------------------


def test_valid_transitions_are_allowed():
    assert "processing" in ALLOWED_TRANSITIONS["queued"]
    assert "completed" in ALLOWED_TRANSITIONS["processing"]
    assert "failed" in ALLOWED_TRANSITIONS["processing"]


def test_terminal_states_allow_no_further_transitions():
    assert ALLOWED_TRANSITIONS[STATUS_COMPLETED] == frozenset()
    assert ALLOWED_TRANSITIONS[STATUS_FAILED] == frozenset()


def test_skipping_the_processing_state_is_illegal():
    """queued -> completed directly is not a transition the graph allows —
    the same discipline resume's ALLOWED_TRANSITIONS enforces."""
    run = JobProcessingRun(job_id=1, attempt=1)
    with pytest.raises(pipeline.IllegalTransition):
        pipeline.advance(run, STATUS_COMPLETED)


def test_leaving_a_terminal_state_is_illegal():
    run = JobProcessingRun(job_id=1, attempt=1)
    run.status = STATUS_COMPLETED
    with pytest.raises(pipeline.IllegalTransition):
        pipeline.advance(run, STATUS_QUEUED)


# --- the happy path ------------------------------------------------------


def test_a_job_with_a_description_completes_and_produces_requirements(db, user):
    job = make_job(db, user)
    run = make_run(db, job)

    finished = pipeline.process_run(db, run_id=run.id)

    assert finished.status == "completed"
    assert finished.started_at is not None
    assert finished.finished_at is not None
    assert finished.failed_stage is None

    stored = db.query(JobRequirements).filter_by(job_processing_run_id=run.id).one()
    assert stored.structurer == "deterministic"
    assert stored.payload is not None


def test_every_stage_is_timed(db, user):
    job = make_job(db, user)
    run = make_run(db, job)
    pipeline.process_run(db, run_id=run.id)

    events = db.query(JobProcessingEvent).filter_by(job_processing_run_id=run.id).all()
    stages = {e.stage for e in events}
    assert {"structure", "materialise"} <= stages
    assert all(e.duration_ms is not None for e in events if e.outcome == "succeeded")


def test_a_job_with_no_description_completes_with_empty_requirements(db, user):
    job = make_job(db, user, description_text=None)
    run = make_run(db, job)

    finished = pipeline.process_run(db, run_id=run.id)

    assert finished.status == "completed"
    stored = db.query(JobRequirements).filter_by(job_processing_run_id=run.id).one()
    assert all(len(v) == 0 for v in stored.payload.values())


# --- failure ---------------------------------------------------------------


def test_a_structuring_error_fails_the_run_at_the_structure_stage(db, user):
    job = make_job(db, user)
    run = make_run(db, job)

    failing = FakeStructurer(error=StructuringError("bad_input", "could not read it"))
    finished = pipeline.process_run(db, run_id=run.id, structurer=failing)

    assert finished.status == "failed"
    assert finished.failed_stage == "structure"
    assert finished.error_code == "bad_input"
    assert finished.finished_at is not None
    # No requirements row for a failed run.
    assert db.query(JobRequirements).filter_by(job_processing_run_id=run.id).count() == 0


def test_an_unexpected_exception_still_ends_the_run(db, user):
    """A run left non-terminal is worse than a failed one — the same
    discipline the resume pipeline's generic exception guard enforces."""
    job = make_job(db, user)
    run = make_run(db, job)

    class Exploding:
        name = "exploding"
        model = None

        def structure(self, job):
            raise RuntimeError("the job description that must not be logged")

    finished = pipeline.process_run(db, run_id=run.id, structurer=Exploding())

    assert finished.status == "failed"
    assert finished.failed_stage == "structure"
    assert finished.error_code == "unexpected_error"
    # Our own words, never the library's.
    assert "must not be logged" not in (finished.error_message or "")


def test_a_failed_run_records_which_stage_and_does_not_crash_the_caller(db, user):
    job = make_job(db, user)
    run = make_run(db, job)
    failing = FakeStructurer(error=StructuringError("x", "y" * 1000))

    finished = pipeline.process_run(db, run_id=run.id, structurer=failing)

    # The message is truncated to the column's own limit.
    assert len(finished.error_message) <= 400


def test_a_provider_error_travels_unchanged_into_the_run(db, user):
    """The provider's own vocabulary (app/services/ai/providers/base.py) is
    already this domain's vocabulary — failures.py classifies these codes
    directly — so a ProviderError is not flattened into one generic code."""
    job = make_job(db, user)
    run = make_run(db, job)
    failing = FakeStructurer(error=ProviderError("ai_rate_limited", "slow down"))

    finished = pipeline.process_run(db, run_id=run.id, structurer=failing)

    assert finished.status == "failed"
    assert finished.failed_stage == "structure"
    assert finished.error_code == "ai_rate_limited"


def test_a_permanent_provider_error_is_classified_as_such_in_the_stage_event(db, user):
    job = make_job(db, user)
    run = make_run(db, job)
    failing = FakeStructurer(error=ProviderError("ai_content_rejected", "declined"))

    pipeline.process_run(db, run_id=run.id, structurer=failing)

    event = (
        db.query(JobProcessingEvent)
        .filter_by(job_processing_run_id=run.id, stage="structure", outcome="failed")
        .one()
    )
    assert event.detail["classification"] == "permanent"


# --- retry / reprocess: history preserved -------------------------------


def test_reprocessing_never_overwrites_the_earlier_attempt(db, user):
    job = make_job(db, user)
    first = make_run(db, job, attempt=1)
    pipeline.process_run(db, run_id=first.id, structurer=FakeStructurer(error=StructuringError("x", "boom")))

    second = make_run(db, job, attempt=2)
    pipeline.process_run(db, run_id=second.id)

    runs = db.query(JobProcessingRun).filter_by(job_id=job.id).order_by(JobProcessingRun.attempt).all()
    assert [(r.attempt, r.status) for r in runs] == [(1, "failed"), (2, "completed")]
    # Attempt 1's failure record is untouched.
    assert runs[0].error_code == "x"


def test_repeated_failures_are_each_preserved_individually(db, user):
    job = make_job(db, user)
    codes = ["a", "b", "c"]
    for attempt, code in enumerate(codes, start=1):
        run = make_run(db, job, attempt=attempt)
        pipeline.process_run(db, run_id=run.id, structurer=FakeStructurer(error=StructuringError(code, "x")))

    runs = db.query(JobProcessingRun).filter_by(job_id=job.id).order_by(JobProcessingRun.attempt).all()
    assert [r.error_code for r in runs] == codes


# --- provenance survives materialisation ----------------------------------


def test_materialised_requirements_carry_the_prompt_version_when_one_was_used(db, user):
    """Deterministic runs have no prompt; a provider-backed run's prompt_version
    must survive into the stored row so a later audit can say which wording
    produced it — see JobStructuringResult and app/schemas/job_candidate.py's
    schema-versioning discipline."""
    job = make_job(db, user)
    run = make_run(db, job)

    class VersionedStructurer:
        name = "provider"
        model = "fake-model"

        def structure(self, job):
            return JobStructuringResult(
                candidate=JobCandidate(),
                structurer=self.name,
                provider="fake",
                model=self.model,
                prompt_version="2099-01-01.1",
            )

    pipeline.process_run(db, run_id=run.id, structurer=VersionedStructurer())

    stored = db.query(JobRequirements).filter_by(job_processing_run_id=run.id).one()
    assert stored.prompt_version == "2099-01-01.1"
    assert stored.provider == "fake"


def test_materialised_requirements_keep_their_provenance(db, user):
    job = make_job(db, user, description_text="Requirements:\n- Experience with Python\n")
    run = make_run(db, job)
    pipeline.process_run(db, run_id=run.id)

    stored = db.query(JobRequirements).filter_by(job_processing_run_id=run.id).one()
    tech = stored.payload["technologies"][0]
    assert tech["provenance"][0]["quote"] == "Experience with Python"
    assert tech["provenance"][0]["verified"] is True
