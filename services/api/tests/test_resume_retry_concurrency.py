"""Retry safety under concurrency and restart.

These tests use real, separate database connections rather than the shared
savepoint session the rest of the suite runs on. That session is one connection
inside a transaction nobody else can see, which is exactly the wrong instrument
for asking "what happens when two processes do this at once" — it would prove
only that one connection is consistent with itself.

So each test here commits, and cleans up after itself in a finally block.
"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import ResumeDocument, ResumeProcessingRun, User
from app.services.resume import lease, reaper, retry, service
from app.services.resume.backoff import BackoffPolicy
from app.services.resume.queue import ExternalQueue
from app.services.storage import InMemoryStorage
from tests.resume_fixtures import make_resume_pdf

EMAIL = "retry-concurrency@example.com"


@pytest.fixture
def sessions(engine):
    """A factory for independent, committing sessions on the real engine."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    made = []

    def _make():
        session = factory()
        made.append(session)
        return session

    yield _make

    for session in made:
        session.close()


@pytest.fixture
def scenario(engine, sessions):
    """A user, a stored document, and a failed first attempt — committed.

    Yields (session, document, failed_run) and removes everything afterwards, so
    these tests leave the shared database as they found it.
    """
    storage = InMemoryStorage()
    db = sessions()

    def purge():
        with engine.begin() as connection:
            user_id = connection.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": EMAIL}
            ).scalar()
            if user_id is None:
                return
            connection.execute(
                text(
                    "DELETE FROM resume_processing_runs WHERE resume_document_id IN"
                    " (SELECT id FROM resume_documents WHERE user_id = :u)"
                ),
                {"u": user_id},
            )
            connection.execute(
                text("DELETE FROM resume_documents WHERE user_id = :u"), {"u": user_id}
            )
            connection.execute(
                text(
                    "DELETE FROM profile_versions WHERE profile_id IN"
                    " (SELECT id FROM profiles WHERE user_id = :u)"
                ),
                {"u": user_id},
            )
            connection.execute(
                text("DELETE FROM profiles WHERE user_id = :u"), {"u": user_id}
            )
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})

    purge()
    user = User(email=EMAIL)
    db.add(user)
    db.commit()

    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
        storage=storage,
    )
    run = service.start_run(db, document=document)
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_rate_limited"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    try:
        yield db, document, run
    finally:
        purge()


def runs_for(db, document_id):
    db.expire_all()
    return list(
        db.scalars(
            select(ResumeProcessingRun)
            .where(ResumeProcessingRun.resume_document_id == document_id)
            .order_by(ResumeProcessingRun.attempt)
        )
    )


# --- scheduling ---------------------------------------------------------------


def test_two_schedulers_reacting_to_one_failure_open_a_single_retry(
    scenario, sessions
):
    """The requirement: two workers cannot schedule duplicate retries.

    Both derive the successor's number from the SAME failed attempt, so both
    compute the same value and UNIQUE(resume_document_id, attempt) lets exactly
    one insert win. Deriving it from max()+1 instead would let them compute 2
    and 3 and both succeed — two retries for one failure.
    """
    db, document, failed = scenario
    other = sessions()
    other_failed = other.get(ResumeProcessingRun, failed.id)

    first = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())
    second = retry.schedule_retry(other, failed_run=other_failed, queue=ExternalQueue())

    assert first is not None
    assert second is None, "the loser must not open a second successor"

    attempts = [(r.attempt, r.status) for r in runs_for(db, document.id)]
    assert attempts == [(1, "failed"), (2, "uploaded")]


def test_concurrent_schedulers_in_real_threads_open_a_single_retry(
    scenario, sessions, engine
):
    """The same claim, made by threads racing on separate connections."""
    db, document, failed = scenario
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    opened = []
    barrier = threading.Barrier(4)

    def schedule():
        session = factory()
        try:
            run = session.get(ResumeProcessingRun, failed.id)
            barrier.wait(timeout=10)
            opened.append(
                retry.schedule_retry(session, failed_run=run, queue=ExternalQueue())
            )
        except Exception:  # pragma: no cover - surfaced by the assertions below
            opened.append(None)
        finally:
            session.close()

    threads = [threading.Thread(target=schedule) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sum(1 for run in opened if run is not None) == 1, opened
    assert [r.attempt for r in runs_for(db, document.id)] == [1, 2]


def test_a_manual_attempt_racing_an_automatic_one_does_not_double(
    scenario, sessions
):
    """A person clicking retry and the policy scheduling one are the same race.

    The manual path allocates max()+1 and the automatic path allocates
    failed.attempt+1, which for a single failed attempt are the same number — so
    the constraint arbitrates and the document still gets one successor.
    """
    db, document, failed = scenario
    other = sessions()

    manual = service.start_run(other, document=other.get(ResumeDocument, document.id))
    automatic = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())

    assert manual is not None
    assert automatic is None
    assert [r.attempt for r in runs_for(db, document.id)] == [1, 2]


# --- executing -----------------------------------------------------------------


def test_two_workers_cannot_execute_the_same_retry(scenario, sessions):
    """Once a retry is due, claiming it is still one atomic UPDATE."""
    db, document, failed = scenario
    scheduled = retry.schedule_retry(
        db,
        failed_run=failed,
        queue=ExternalQueue(),
        backoff=BackoffPolicy(0.0, 2.0, 0.0, 0.0),  # due immediately
    )
    assert scheduled.next_retry_at is None

    other = sessions()
    assert lease.acquire(db, run_id=scheduled.id, owner="worker-a", lease_seconds=300)
    assert not lease.acquire(
        other, run_id=scheduled.id, owner="worker-b", lease_seconds=300
    )


def test_concurrent_workers_in_real_threads_claim_a_due_retry_once(
    scenario, engine
):
    db, document, failed = scenario
    scheduled = retry.schedule_retry(
        db,
        failed_run=failed,
        queue=ExternalQueue(),
        backoff=BackoffPolicy(0.0, 2.0, 0.0, 0.0),
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    won = []
    barrier = threading.Barrier(6)

    def claim(index):
        session = factory()
        try:
            barrier.wait(timeout=10)
            if lease.acquire(
                session, run_id=scheduled.id, owner=f"worker-{index}", lease_seconds=300
            ):
                won.append(index)
        finally:
            session.close()

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(won) == 1, won


def test_an_expired_lease_does_not_cause_an_early_retry(scenario, sessions):
    """Recovery is not a shortcut past the backoff.

    A worker dying mid-run fails that attempt and opens the next one — and the
    next one waits exactly as long as any other retry, because `worker_abandoned`
    is a transient code like any other rather than a special case.
    """
    db, document, failed = scenario

    # A second attempt, claimed by a worker that then goes away.
    second = retry.schedule_retry(
        db,
        failed_run=failed,
        queue=ExternalQueue(),
        backoff=BackoffPolicy(0.0, 2.0, 0.0, 0.0),
    )
    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(
        db, run_id=second.id, owner="doomed-worker", lease_seconds=60, now=long_ago
    )

    queue = ExternalQueue()
    recoveries = reaper.recover_abandoned_runs(db, queue=queue)
    assert [r.kind for r in recoveries] == [reaper.ABANDONED]

    third = db.get(ResumeProcessingRun, recoveries[0].retry_run_id)
    assert third.next_retry_at is not None, "recovery must not skip the backoff"
    assert not lease.acquire(
        db, run_id=third.id, owner="eager-worker", lease_seconds=60
    )
    assert queue.enqueued == []


# --- restart -------------------------------------------------------------------


def test_a_scheduled_retry_survives_a_process_restart(scenario, sessions, engine):
    """Nothing is held in memory, so there is nothing for a restart to lose.

    The scheduling session is closed and discarded — every queue, worker and
    reaper object with it — and the retry is then found and run by processes
    that never saw it scheduled.
    """
    db, document, failed = scenario
    scheduled = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())
    scheduled_id = scheduled.id
    due_at = scheduled.next_retry_at
    assert due_at is not None

    # The process that scheduled it goes away entirely.
    db.close()

    after_restart = sessions()
    survivor = after_restart.get(ResumeProcessingRun, scheduled_id)
    assert survivor is not None
    assert survivor.next_retry_at == due_at
    assert survivor.status == "uploaded"

    # A brand new reaper, which has never heard of this run, releases it when due.
    queue = ExternalQueue()
    assert reaper.recover_abandoned_runs(after_restart, queue=queue) == []

    later = due_at + timedelta(seconds=1)
    recoveries = reaper.recover_abandoned_runs(after_restart, queue=queue, now=later)
    assert [r.run_id for r in recoveries] == [scheduled_id]
    assert queue.enqueued == [scheduled_id]


# --- exhaustion ----------------------------------------------------------------


def test_the_attempt_ceiling_still_holds_when_schedulers_race(
    scenario, sessions, monkeypatch
):
    """Two schedulers cannot together push a document past its budget."""
    db, document, failed = scenario
    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 2)

    other = sessions()
    other_failed = other.get(ResumeProcessingRun, failed.id)

    first = retry.schedule_retry(db, failed_run=failed, queue=ExternalQueue())
    second = retry.schedule_retry(other, failed_run=other_failed, queue=ExternalQueue())
    assert (first is not None) and (second is None)

    # The successor fails too. The budget is now spent, and neither session may
    # open another.
    first.status = "failed"
    first.failed_stage = "structure"
    first.error_code = "ai_rate_limited"
    first.finished_at = datetime.now(timezone.utc)
    db.commit()

    assert retry.schedule_retry(db, failed_run=first, queue=ExternalQueue()) is None
    assert retry.is_exhausted(
        db, document_id=document.id, error_code="ai_rate_limited"
    )
    assert [r.attempt for r in runs_for(db, document.id)] == [1, 2]
