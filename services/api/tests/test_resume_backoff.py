"""The retry backoff: the formula, the bounds, and when a run becomes eligible.

The policy is pure arithmetic, so most of this needs no database. The parts that
do are the parts that matter operationally: that a scheduled retry is not
claimable before its time, that it is afterwards, and that both facts live in
the row rather than in any process's memory.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models import ResumeProcessingRun
from app.services.resume import lease, reaper, retry, service
from app.services.resume.backoff import BackoffPolicy
from app.services.resume.queue import ExternalQueue
from app.services.resume.worker import ResumeWorker
from app.services.storage import InMemoryStorage
from tests.resume_fixtures import make_resume_pdf

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def policy():
    return BackoffPolicy(
        base_seconds=30.0, multiplier=2.0, max_seconds=900.0, jitter=0.5
    )


def upload(db, user, storage, data=None):
    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=data if data is not None else make_resume_pdf(),
        storage=storage,
    )
    return document


def fail(db, run, code="stored_object_missing", stage="validate"):
    run.status = "failed"
    run.failed_stage = stage
    run.error_code = code
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run


# --- the formula -------------------------------------------------------------


def test_the_delay_grows_exponentially_with_the_attempt(policy):
    """Each attempt gives whatever is failing longer to stop failing."""
    assert policy.ceiling_for(2) == 30.0
    assert policy.ceiling_for(3) == 60.0
    assert policy.ceiling_for(4) == 120.0
    assert policy.ceiling_for(5) == 240.0


def test_the_first_attempt_is_not_a_retry_and_waits_for_nothing(policy):
    assert policy.ceiling_for(1) == 0.0
    assert policy.next_retry_at(1, now=NOW) is None


def test_the_maximum_delay_is_respected(policy):
    """Doubling without a ceiling reaches hours, and an hourly retry is
    indistinguishable from no retry to whoever is waiting."""
    for attempt in range(7, 30):
        assert policy.ceiling_for(attempt) == 900.0


def test_a_runaway_multiplier_cannot_overflow_past_the_ceiling():
    """Float overflow is still just 'longer than the maximum'."""
    steep = BackoffPolicy(base_seconds=30.0, multiplier=10.0, max_seconds=900.0, jitter=0.0)
    assert steep.ceiling_for(400) == 900.0


def test_jitter_keeps_the_delay_inside_its_stated_bounds(policy):
    """The bound is assertable, not merely probable: jitter is subtractive, so
    the configured formula is a ceiling rather than a midpoint."""
    import random

    rng = random.Random(1234)
    low, high = policy.bounds_for(3)
    assert (low, high) == (30.0, 60.0)

    for _ in range(500):
        delay = policy.delay_for(3, rng=rng)
        assert low <= delay <= high


def test_more_jitter_never_lengthens_the_worst_case():
    """Turning jitter up must not turn a 15-minute maximum into a 30-minute one."""
    for jitter in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = BackoffPolicy(30.0, 2.0, 900.0, jitter)
        assert p.bounds_for(4)[1] == 120.0
        assert min(p.delay_for(4) for _ in range(200)) >= 0.0


def test_zero_jitter_is_deterministic():
    """Useful in a test, and a legitimate production choice for one worker."""
    p = BackoffPolicy(30.0, 2.0, 900.0, 0.0)
    assert {p.delay_for(3) for _ in range(50)} == {60.0}


def test_full_jitter_can_spread_across_the_whole_window():
    p = BackoffPolicy(30.0, 2.0, 900.0, 1.0)
    samples = [p.delay_for(3) for _ in range(500)]
    assert min(samples) < 6.0 and max(samples) > 54.0
    assert all(0.0 <= s <= 60.0 for s in samples)


def test_jitter_outside_its_range_is_clamped_not_obeyed():
    """A bad setting is a configuration mistake, and the worst time to discover
    one is while recovering from the outage that triggered the retry."""
    absurd = BackoffPolicy(30.0, 2.0, 900.0, 5.0)
    assert absurd.bounds_for(3) == (0.0, 60.0)
    assert all(0.0 <= absurd.delay_for(3) <= 60.0 for _ in range(100))

    negative = BackoffPolicy(30.0, 2.0, 900.0, -2.0)
    assert negative.delay_for(3) == 60.0


def test_a_zero_delay_schedules_nothing_rather_than_now(policy):
    """NULL means 'nothing is holding this back'. A timestamp that merely
    happens to be in the past would make every claim query carry a comparison
    it does not need."""
    off = BackoffPolicy(0.0, 2.0, 0.0, 0.5)
    assert off.next_retry_at(3, now=NOW) is None


def test_the_scheduled_time_is_the_delay_after_now(policy):
    scheduled = policy.next_retry_at(3, now=NOW)
    assert scheduled is not None
    assert timedelta(seconds=30) <= scheduled - NOW <= timedelta(seconds=60)


def test_the_policy_reads_its_numbers_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "RESUME_RETRY_BACKOFF_BASE_SECONDS", 7.0)
    monkeypatch.setattr(settings, "RESUME_RETRY_BACKOFF_MULTIPLIER", 3.0)
    monkeypatch.setattr(settings, "RESUME_RETRY_BACKOFF_MAX_SECONDS", 50.0)
    monkeypatch.setattr(settings, "RESUME_RETRY_JITTER", 0.0)

    configured = BackoffPolicy.from_settings()
    assert configured.ceiling_for(2) == 7.0
    assert configured.ceiling_for(3) == 21.0
    assert configured.ceiling_for(4) == 50.0  # capped


# --- scheduling, against the database ----------------------------------------


def test_a_transient_failure_is_scheduled_not_run(db, user, storage):
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))

    queue = ExternalQueue()
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=queue)

    assert scheduled.next_retry_at is not None
    assert scheduled.status == "uploaded"
    assert scheduled.started_at is None
    # Not offered to anyone: a run that must be refused should not be handed out.
    assert queue.enqueued == []


def test_a_permanent_failure_is_never_delayed_because_it_is_never_retried(
    db, user, storage
):
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document), code="malformed_pdf")

    assert retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue()) is None
    # No successor at all, so nothing was scheduled and nothing waits.
    runs = db.query(ResumeProcessingRun).filter_by(
        resume_document_id=document.id
    ).all()
    assert [(r.attempt, r.next_retry_at) for r in runs] == [(1, None)]


def test_an_unknown_failure_code_is_never_delayed_either(db, user, storage):
    """Unclassified is treated as permanent, so it gets no retry and therefore
    no schedule."""
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document), code="a_new_thing")

    assert retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue()) is None


def test_the_scheduled_time_lengthens_with_each_attempt(
    db, user, storage, monkeypatch
):
    """Attempt 2 waits one base delay, attempt 3 waits two."""
    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(settings, "RESUME_RETRY_JITTER", 0.0)
    document = upload(db, user, storage)

    run = service.start_run(db, document=document)
    waits = []
    for _ in range(3):
        failed = fail(db, run)
        before = datetime.now(timezone.utc)
        run = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())
        waits.append((run.next_retry_at - before).total_seconds())

    assert waits[0] < waits[1] < waits[2]
    assert waits[0] == pytest.approx(30, abs=2)
    assert waits[1] == pytest.approx(60, abs=2)
    assert waits[2] == pytest.approx(120, abs=2)


# --- eligibility --------------------------------------------------------------


def test_a_scheduled_retry_is_not_claimable_before_its_time(db, user, storage):
    """The guarantee, at the level that actually decides: no worker can take it."""
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    assert not lease.acquire(
        db, run_id=scheduled.id, owner="eager-worker", lease_seconds=60
    )
    assert scheduled.id not in lease.claimable_run_ids(db, limit=10)
    assert lease.due_retry_runs(db, limit=10) == []


def test_a_worker_handed_a_not_yet_due_run_refuses_it(db, user, storage):
    """Belt and braces: even a stale enqueue that survived a restart cannot
    start a retry early, because the row itself refuses."""
    from contextlib import contextmanager

    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    @contextmanager
    def scope():
        yield db

    worker = ResumeWorker(
        session_scope=scope, worker_id="w", heartbeat_seconds=0, storage=storage
    )
    outcome = worker.execute(scheduled.id)

    assert outcome.acquired is False
    db.expire_all()
    assert db.get(ResumeProcessingRun, scheduled.id).status == "uploaded"


def test_a_scheduled_retry_becomes_claimable_once_its_time_passes(db, user, storage):
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    later = scheduled.next_retry_at + timedelta(seconds=1)

    assert scheduled.id in lease.claimable_run_ids(db, limit=10, now=later)
    assert [r.id for r in lease.due_retry_runs(db, limit=10, now=later)] == [scheduled.id]
    assert lease.acquire(
        db, run_id=scheduled.id, owner="worker-a", lease_seconds=60, now=later
    )


def test_the_reaper_releases_a_retry_exactly_when_it_comes_due(db, user, storage):
    """What turns a persisted timestamp back into work, without a scheduler."""
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    queue = ExternalQueue()
    assert reaper.recover_abandoned_runs(db, queue=queue) == []
    assert queue.enqueued == []

    later = scheduled.next_retry_at + timedelta(seconds=1)
    recoveries = reaper.recover_abandoned_runs(db, queue=queue, now=later)

    assert [r.kind for r in recoveries] == [reaper.DUE]
    assert queue.enqueued == [scheduled.id]


def test_a_waiting_retry_is_never_mistaken_for_an_abandoned_one(db, user, storage):
    """Orphan recovery must not override a backoff.

    A scheduled retry and a lost enqueue look identical on every column except
    next_retry_at: both are non-terminal, unleased and never started. If the
    orphan sweep did not exclude them, the grace period would quietly become the
    real delay and the configured backoff would never be honoured.
    """
    document = upload(db, user, storage)
    failed = fail(db, service.start_run(db, document=document))
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    # Pretend it was created long enough ago to be well past the orphan grace
    # period, while its own retry time is still in the future.
    db.query(ResumeProcessingRun).filter_by(id=scheduled.id).update(
        {"created_at": datetime.now(timezone.utc) - timedelta(days=1)}
    )
    db.commit()

    way_past_grace = datetime.now(timezone.utc) + timedelta(hours=1)
    assert lease.unclaimed_runs(db, limit=10, older_than=way_past_grace) == []

    # And the reaper leaves it alone entirely until its own time arrives.
    queue = ExternalQueue()
    assert reaper.recover_abandoned_runs(db, queue=queue) == []
    assert queue.enqueued == []


def test_nothing_sleeps_while_a_retry_waits(db, user, storage):
    """The worker released the run and returned. If backoff were implemented
    with sleep(), this call would take as long as the delay."""
    import time
    from contextlib import contextmanager

    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    storage.delete(document.storage_key)

    @contextmanager
    def scope():
        yield db

    worker = ResumeWorker(
        session_scope=scope,
        worker_id="w",
        heartbeat_seconds=0,
        storage=storage,
        queue=ExternalQueue(),
    )

    started = time.perf_counter()
    outcome = worker.execute(run.id)
    elapsed = time.perf_counter() - started

    assert outcome.retry_run_id is not None
    scheduled = db.get(ResumeProcessingRun, outcome.retry_run_id)
    assert scheduled.next_retry_at is not None
    # The scheduled wait is tens of seconds; the call is not.
    assert elapsed < 5.0
