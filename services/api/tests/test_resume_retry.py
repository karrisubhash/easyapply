"""The retry policy.

Two things are being asserted throughout: that a failure is only retried when
another attempt could genuinely differ, and that retrying never costs us the
record of what already happened.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models import ResumeProcessingRun
from app.services.resume import failures, retry, service
from app.services.resume.queue import ExternalQueue
from app.services.resume.worker import ResumeWorker
from app.services.storage import InMemoryStorage
from tests.resume_fixtures import make_image_only_pdf, make_resume_pdf


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def scope(db):
    @contextmanager
    def _scope():
        yield db

    return _scope


def upload(db, user, storage, data=None, filename="resume.pdf"):
    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename=filename,
        declared_media_type="application/pdf",
        data=data if data is not None else make_resume_pdf(),
        storage=storage,
    )
    return document


def attempts(db, document):
    return [
        (run.attempt, run.status, run.error_code)
        for run in db.query(ResumeProcessingRun)
        .filter_by(resume_document_id=document.id)
        .order_by(ResumeProcessingRun.attempt)
        .all()
    ]


# --- classification ----------------------------------------------------------


def test_infrastructure_failures_are_transient():
    """The document was fine and something around it was not."""
    for code in ("stored_object_missing", "worker_abandoned", "unexpected_error"):
        assert failures.is_retryable(code), code
        assert failures.classification(code) == "transient"


def test_provider_outages_are_transient_but_bad_requests_are_not():
    """An overloaded provider is a bad minute; a rejected request is our bug."""
    for code in ("ai_provider_unavailable", "ai_timeout", "ai_rate_limited"):
        assert failures.is_retryable(code), code

    for code in ("ai_request_invalid", "ai_unauthorized", "ai_not_configured"):
        assert not failures.is_retryable(code), code


def test_a_document_we_cannot_read_is_permanent():
    """The same bytes through the same code reach the same conclusion."""
    for code in (
        "unsupported_file_type",
        "malformed_pdf",
        "document_encrypted",
        "no_text_extracted",
        "ocr_unavailable",
        "result_schema_invalid",
    ):
        assert not failures.is_retryable(code), code
        assert failures.classification(code) == "permanent"


def test_an_unrecognised_code_is_not_retried():
    """The deliberate direction to be wrong in: a failure mode nobody has
    classified is visible and answered, rather than retried blindly."""
    assert not failures.is_retryable("something_new_we_have_not_seen")
    assert failures.classification("something_new_we_have_not_seen") == "unclassified"
    assert not failures.is_retryable(None)


def test_every_classified_code_is_in_exactly_one_set():
    """Two sets that overlap would make is_retryable depend on lookup order."""
    assert not (failures.TRANSIENT_ERROR_CODES & failures.PERMANENT_ERROR_CODES)


def test_every_provider_error_code_is_classified():
    """A provider adapter cannot invent a code the retry policy has no opinion
    about — that would silently mean 'permanent' for a real outage."""
    from app.services.resume.providers import PROVIDER_ERROR_CODES

    known = failures.TRANSIENT_ERROR_CODES | failures.PERMANENT_ERROR_CODES
    assert PROVIDER_ERROR_CODES <= known


# --- the policy --------------------------------------------------------------


def test_a_transient_failure_opens_a_new_attempt(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    run.status = "failed"
    run.failed_stage = "validate"
    run.error_code = "stored_object_missing"
    from datetime import datetime, timezone

    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    queue = ExternalQueue()
    retry_run = retry.schedule_retry(db, failed_run=run, queue=queue)

    assert retry_run is not None
    assert retry_run.attempt == 2
    # Scheduled, not started: the successor carries its own start time and is
    # deliberately NOT handed to the queue, because a run that must be refused
    # should not have been offered.
    assert retry_run.next_retry_at is not None
    assert not retry_run.is_due_at(datetime.now(timezone.utc))
    assert queue.enqueued == []
    # The first attempt is untouched. This is the auditability requirement.
    assert attempts(db, document) == [
        (1, "failed", "stored_object_missing"),
        (2, "uploaded", None),
    ]


def test_a_permanent_failure_opens_nothing(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    run.status = "failed"
    run.failed_stage = "extract"
    run.error_code = "malformed_pdf"
    from datetime import datetime, timezone

    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    queue = ExternalQueue()
    assert retry.schedule_retry(db, failed_run=run, queue=queue) is None
    assert queue.enqueued == []
    assert attempts(db, document) == [(1, "failed", "malformed_pdf")]


def test_the_attempt_ceiling_is_enforced(db, user, storage, monkeypatch, immediate_retries):
    """Bounded, or a permanently broken environment consumes a worker forever.

    Drives the real chain — each failure asks the policy for a successor —
    rather than opening runs by hand, so the count under test is the one the
    policy actually sees.
    """
    from datetime import datetime, timezone

    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 3)
    document = upload(db, user, storage)

    run = service.start_run(db, document=document)
    successors = []
    for _ in range(4):
        run.status = "failed"
        run.failed_stage = "validate"
        run.error_code = "stored_object_missing"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()

        successor = retry.schedule_retry(db, failed_run=run, queue=ExternalQueue())
        successors.append(successor)
        if successor is None:
            break
        run = successor

    # Attempts 1 and 2 earn a successor; attempt 3 is the ceiling.
    assert [bool(s) for s in successors] == [True, True, False]
    assert [attempt for attempt, _, _ in attempts(db, document)] == [1, 2, 3]


def test_a_failed_worker_run_schedules_its_own_retry(db, user, storage, scope):
    """End to end: the worker fails a run and the next attempt is scheduled."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    storage.delete(document.storage_key)  # transient: the object went missing

    queue = ExternalQueue()
    worker = ResumeWorker(
        session_scope=scope,
        worker_id="worker-a",
        heartbeat_seconds=0,
        storage=storage,
        queue=queue,
    )
    outcome = worker.execute(run.id)

    assert outcome.status == "failed"
    assert outcome.error_code == "stored_object_missing"
    assert outcome.retry_run_id is not None

    # The worker let go rather than sleeping: the successor exists, waits, and
    # is not on the queue.
    scheduled = db.get(ResumeProcessingRun, outcome.retry_run_id)
    assert scheduled.next_retry_at is not None
    assert not scheduled.is_due_at(datetime.now(timezone.utc))
    assert queue.enqueued == []


def test_a_permanently_failed_worker_run_schedules_nothing(db, user, storage, scope):
    document = upload(db, user, storage, data=make_image_only_pdf(1), filename="scan.pdf")
    run = service.start_run(db, document=document)

    queue = ExternalQueue()
    worker = ResumeWorker(
        session_scope=scope,
        worker_id="worker-a",
        heartbeat_seconds=0,
        storage=storage,
        queue=queue,
    )
    outcome = worker.execute(run.id)

    assert outcome.status == "failed"
    assert outcome.error_code == "ocr_unavailable"
    assert outcome.retry_run_id is None
    assert queue.enqueued == []


def test_every_attempt_survives_a_run_of_failures(db, user, storage, scope, immediate_retries):
    """The history in the acceptance criteria: failed, failed, completed.

    The retry chain drives itself. Each failure schedules the next attempt, and
    the storage outage ends before the third — so what is asserted is the whole
    sequence the database kept, not three runs a test opened by hand.
    """
    document = upload(db, user, storage)
    key = document.storage_key
    payload = storage.get(key)

    queue = ExternalQueue()
    worker = ResumeWorker(
        session_scope=scope,
        worker_id="worker-a",
        heartbeat_seconds=0,
        storage=storage,
        queue=queue,
    )

    storage.delete(key)  # the outage begins
    run_id = service.start_run(db, document=document, queue=queue).id

    for _ in range(5):
        outcome = worker.execute(run_id)
        if outcome.retry_run_id is None:
            break
        run_id = outcome.retry_run_id
        # The outage ends before the third attempt reaches storage.
        if len(attempts(db, document)) == 3:
            storage.put(key, payload, content_type="application/pdf")

    assert attempts(db, document) == [
        (1, "failed", "stored_object_missing"),
        (2, "failed", "stored_object_missing"),
        (3, "completed", None),
    ]


# --- manual retries ----------------------------------------------------------


def test_a_person_may_retry_a_transient_failure_past_the_automatic_ceiling(
    db, user, storage, monkeypatch
):
    """They know something we do not: the outage is over."""
    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 1)
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    from datetime import datetime, timezone

    run.status = "failed"
    run.failed_stage = "validate"
    run.error_code = "stored_object_missing"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    # Automatic policy declines...
    assert retry.schedule_retry(db, failed_run=run, queue=ExternalQueue()) is None
    # ...but a person is not refused.
    assert retry.manual_retry_refusal(run) is None


def test_a_person_may_not_retry_a_run_still_in_flight(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    assert "already being processed" in retry.manual_retry_refusal(run)


def test_a_person_may_not_retry_a_permanent_failure(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    from datetime import datetime, timezone

    run.status = "failed"
    run.failed_stage = "extract"
    run.error_code = "unsupported_file_type"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    assert "will not change" in retry.manual_retry_refusal(run)


def test_a_completed_run_may_be_reprocessed(db, user, storage, scope):
    """Reading a resume again is a legitimate thing to ask for."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    ResumeWorker(
        session_scope=scope, worker_id="w", heartbeat_seconds=0, storage=storage
    ).execute(run.id)

    db.expire_all()
    assert retry.manual_retry_refusal(db.get(ResumeProcessingRun, run.id)) is None
