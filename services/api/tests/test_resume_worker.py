"""The worker, the queue and the reaper.

The worker is an execution mechanism, so what is worth testing is not what it
computes — the pipeline already has those tests — but what it does around the
work: claiming safely, holding the claim, letting go, and never leaving a run in
a state nothing can recover.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.models import ResumeProcessingRun
from app.services import profile_service
from app.services.resume import lease, reaper, retry, service
from app.services.resume.queue import ExternalQueue, InlineQueue, ThreadPoolQueue
from app.services.resume.worker import Heartbeat, ResumeWorker
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


def make_worker(scope, storage, *, worker_id="worker-a", **kwargs):
    return ResumeWorker(
        session_scope=scope,
        worker_id=worker_id,
        heartbeat_seconds=0,
        storage=storage,
        **kwargs,
    )


def upload(db, user, storage, data=None, filename="resume.pdf", media="application/pdf"):
    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename=filename,
        declared_media_type=media,
        data=data if data is not None else make_resume_pdf(),
        storage=storage,
    )
    return document


# --- executing one run -------------------------------------------------------


def test_the_worker_takes_a_queued_run_to_completion(db, user, storage, scope):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    outcome = make_worker(scope, storage).execute(run.id)

    assert outcome.acquired is True
    assert outcome.status == "completed"
    assert db.get(ResumeProcessingRun, run.id).profile_version_id is not None


def test_the_worker_releases_the_lease_when_it_finishes(db, user, storage, scope):
    """A lease held past the work is a run nobody can recover until it lapses."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    make_worker(scope, storage).execute(run.id)

    db.expire_all()
    finished = db.get(ResumeProcessingRun, run.id)
    assert finished.lease_owner is None
    assert finished.leased_until is None


def test_the_worker_releases_the_lease_even_when_the_pipeline_fails(
    db, user, storage, scope
):
    document = upload(db, user, storage, data=make_image_only_pdf(1), filename="scan.pdf")
    run = service.start_run(db, document=document)

    outcome = make_worker(scope, storage).execute(run.id)

    assert outcome.status == "failed"
    db.expire_all()
    assert db.get(ResumeProcessingRun, run.id).lease_owner is None


def test_a_second_worker_cannot_process_a_run_already_taken(db, user, storage, scope):
    """The requirement, at the worker level: A holds it, B cannot execute it."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    # A claims and keeps the run, as it would while working.
    assert lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=300)

    outcome = make_worker(scope, storage, worker_id="worker-b").execute(run.id)

    assert outcome.acquired is False
    assert outcome.status is None
    # B did not start the pipeline: nothing was extracted and the run is intact.
    db.expire_all()
    untouched = db.get(ResumeProcessingRun, run.id)
    assert untouched.status == "uploaded"
    assert untouched.lease_owner == "worker-a"


def test_a_run_whose_lease_expired_is_processed_by_the_next_worker(
    db, user, storage, scope
):
    """Recovery after a worker dies mid-claim, without the reaper involved."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(db, run_id=run.id, owner="dead-worker", lease_seconds=60, now=past)

    outcome = make_worker(scope, storage, worker_id="worker-b").execute(run.id)
    assert outcome.acquired is True
    assert outcome.status == "completed"


def test_polling_claims_whatever_is_outstanding(db, user, storage, scope):
    """How a standalone worker finds work with no broker: the run table."""
    first = upload(db, user, storage)
    second = upload(db, user, storage, data=make_resume_pdf() + b"\n% second\n")
    service.start_run(db, document=first)
    service.start_run(db, document=second)

    outcomes = make_worker(scope, storage).poll_once(limit=10)

    assert len(outcomes) == 2
    assert all(outcome.status == "completed" for outcome in outcomes)


# --- the heartbeat -----------------------------------------------------------


def test_the_heartbeat_renews_until_it_is_stopped(db, user, storage, scope):
    """Renewal is exercised against the database in test_resume_lease; what
    matters here is that the thread keeps calling it and stops when told."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=300)

    beats = []

    @contextmanager
    def counting_scope():
        beats.append(datetime.now(timezone.utc))
        yield db

    heartbeat = Heartbeat(
        run_id=run.id,
        owner="worker-a",
        interval_seconds=0.01,
        lease_seconds=300,
        session_scope=counting_scope,
    )
    heartbeat.start()
    try:
        _wait_until(lambda: len(beats) >= 2)
    finally:
        heartbeat.stop()

    assert len(beats) >= 2
    # Stopped means stopped: no further renewal after the join returns.
    settled = len(beats)
    _sleep(0.05)
    assert len(beats) == settled


def test_the_heartbeat_gives_up_when_the_run_is_taken_away(db, user, storage):
    """Losing the lease mid-run is the one thing a worker has to notice."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    lease.acquire(db, run_id=run.id, owner="thief", lease_seconds=300)

    @contextmanager
    def scope_for_loser():
        yield db

    heartbeat = Heartbeat(
        run_id=run.id,
        owner="worker-a",  # never held it
        interval_seconds=0.01,
        lease_seconds=300,
        session_scope=scope_for_loser,
    )
    heartbeat.start()
    try:
        _wait_until(heartbeat.lost.is_set)
    finally:
        heartbeat.stop()

    assert heartbeat.lost.is_set()


def test_a_zero_interval_runs_no_heartbeat_thread(db):
    heartbeat = Heartbeat(
        run_id=1,
        owner="worker-a",
        interval_seconds=0,
        lease_seconds=300,
        session_scope=None,
    )
    heartbeat.start()
    heartbeat.stop()
    assert not heartbeat.lost.is_set()


# --- the queue ---------------------------------------------------------------


def test_the_thread_queue_will_not_queue_the_same_run_twice(db):
    """Queued exactly once.

    The lease already makes a duplicate harmless; this is about not spending a
    worker to find that out. A run held by the pool is refused admission until
    it has been handed over.
    """
    import threading

    started = threading.Event()
    release = threading.Event()
    executed = []

    class BlockingWorker:
        queue = None

        def execute(self, run_id):
            executed.append(run_id)
            started.set()
            release.wait(timeout=2)

    queue = ThreadPoolQueue(BlockingWorker(), max_workers=1)
    try:
        assert queue.enqueue(7) is True
        _wait_until(started.is_set)
        # Admitted once; the second offer is refused while the first is in hand.
        assert queue.enqueue(7) is False
    finally:
        release.set()
        queue.shutdown(wait=True)

    assert executed == [7]


def test_the_thread_queue_refuses_work_once_it_is_shutting_down(db):
    """A pool that accepted work during shutdown would drop it silently."""

    class IdleWorker:
        queue = None

        def execute(self, run_id):  # pragma: no cover - never reached
            raise AssertionError("should not run")

    queue = ThreadPoolQueue(IdleWorker(), max_workers=1)
    queue.shutdown(wait=True)

    assert queue.enqueue(1) is False


def test_a_worker_exception_in_the_pool_does_not_kill_the_queue(db):
    """A pool thread's exception vanishes into a Future nobody reads, so the
    queue has to catch it — and keep accepting work afterwards."""
    import threading

    seen = []
    done = threading.Event()

    class FlakyWorker:
        queue = None

        def execute(self, run_id):
            seen.append(run_id)
            if run_id == 1:
                raise RuntimeError("worker fell over")
            done.set()

    queue = ThreadPoolQueue(FlakyWorker(), max_workers=1)
    try:
        queue.enqueue(1)
        queue.enqueue(2)
        _wait_until(done.is_set)
    finally:
        queue.shutdown(wait=True)

    assert seen == [1, 2]


def test_the_inline_queue_runs_the_work_before_it_returns(db, user, storage, scope):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    InlineQueue(make_worker(scope, storage)).enqueue(run.id)

    db.expire_all()
    assert db.get(ResumeProcessingRun, run.id).status == "completed"


def test_the_external_queue_runs_nothing_itself(db, user, storage):
    """The run row is the queue; a separate process claims it."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    queue = ExternalQueue()
    assert queue.enqueue(run.id) is True
    assert queue.enqueued == [run.id]

    db.expire_all()
    assert db.get(ResumeProcessingRun, run.id).status == "uploaded"


# --- the reaper --------------------------------------------------------------


def test_the_reaper_fails_an_abandoned_run_and_opens_a_new_attempt(
    db, user, storage, scope
):
    """A worker died mid-run: the attempt is closed honestly, a fresh one opens
    beside it, and the document does not sit there forever."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(db, run_id=run.id, owner="dead-worker", lease_seconds=60, now=past)

    queue = ExternalQueue()
    recoveries = reaper.recover_abandoned_runs(db, queue=queue)

    assert [r.kind for r in recoveries] == [reaper.ABANDONED]

    db.expire_all()
    abandoned = db.get(ResumeProcessingRun, run.id)
    assert abandoned.status == "failed"
    assert abandoned.error_code == "worker_abandoned"
    assert abandoned.finished_at is not None
    assert abandoned.lease_owner is None

    # A new attempt, not a reopened one — and it waits like any other retry.
    # An expired lease is not a shortcut past the backoff.
    assert recoveries[0].retry_run_id is not None
    retry_run = db.get(ResumeProcessingRun, recoveries[0].retry_run_id)
    assert retry_run.attempt == 2
    assert retry_run.status == "uploaded"
    assert retry_run.next_retry_at is not None
    assert queue.enqueued == []

    # It reaches the queue on the sweep after its wait ends, and not before.
    assert reaper.recover_abandoned_runs(db, queue=queue) == []
    later = retry_run.next_retry_at + timedelta(seconds=1)
    due = reaper.recover_abandoned_runs(db, queue=queue, now=later)
    assert [r.kind for r in due] == [reaper.DUE]
    assert queue.enqueued == [retry_run.id]


def test_the_recovered_attempt_can_then_complete(db, user, storage, scope, immediate_retries):
    """The acceptance path: crash, lease expires, reaper recovers, retry works."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(db, run_id=run.id, owner="dead-worker", lease_seconds=60, now=past)

    recoveries = reaper.recover_abandoned_runs(db, queue=ExternalQueue())
    outcome = make_worker(scope, storage).execute(recoveries[0].retry_run_id)

    assert outcome.status == "completed"

    runs = (
        db.query(ResumeProcessingRun)
        .filter_by(resume_document_id=document.id)
        .order_by(ResumeProcessingRun.attempt)
        .all()
    )
    # The audit trail the requirement asks for: attempt 1 failed, attempt 2 done.
    assert [(r.attempt, r.status) for r in runs] == [(1, "failed"), (2, "completed")]


def test_the_reaper_leaves_a_live_lease_alone(db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    lease.acquire(db, run_id=run.id, owner="busy-worker", lease_seconds=300)

    assert reaper.recover_abandoned_runs(db, queue=ExternalQueue()) == []

    db.expire_all()
    assert db.get(ResumeProcessingRun, run.id).status == "uploaded"


def test_the_reaper_never_touches_a_finished_run(db, user, storage, scope):
    """Terminal runs are the record. A reaper that could rewrite them would be
    a reaper that could rewrite history."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    make_worker(scope, storage).execute(run.id)

    # Force a stale lease onto the completed run.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    db.query(ResumeProcessingRun).filter_by(id=run.id).update(
        {"lease_owner": "ghost", "leased_until": past}
    )
    db.commit()

    assert reaper.recover_abandoned_runs(db, queue=ExternalQueue()) == []
    db.expire_all()
    assert db.get(ResumeProcessingRun, run.id).status == "completed"


def test_a_run_nobody_ever_claimed_is_requeued_not_failed(db, user, storage):
    """A lost enqueue costs latency, not an attempt: nothing was tried, so
    there is nothing to fail and no attempt to burn."""
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)

    queue = ExternalQueue()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    recoveries = reaper.recover_abandoned_runs(db, queue=queue, now=future)

    assert [r.kind for r in recoveries] == [reaper.ORPHANED]
    assert queue.enqueued == [run.id]

    db.expire_all()
    still_queued = db.get(ResumeProcessingRun, run.id)
    assert still_queued.status == "uploaded"
    assert still_queued.attempt == 1


def test_a_recent_unclaimed_run_is_left_for_its_worker(db, user, storage):
    """Inside the grace period a worker may simply not have got to it yet."""
    document = upload(db, user, storage)
    service.start_run(db, document=document)

    assert reaper.recover_abandoned_runs(db, queue=ExternalQueue()) == []


# --- small helpers -----------------------------------------------------------


def _wait_until(predicate, timeout=2.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached in time")


def _sleep(seconds):
    import time

    time.sleep(seconds)
