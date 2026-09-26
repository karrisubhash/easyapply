"""The lease, against a real database.

Every claim here is about what PostgreSQL does, not about what the Python
believes, so nothing is mocked. The concurrency tests do not need threads: two
workers racing is exactly two `acquire` calls, and the point is that the second
one loses whatever order they arrive in.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ResumeProcessingRun
from app.services.resume import lease, service
from app.services.storage import InMemoryStorage
from tests.resume_fixtures import make_resume_pdf


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def run(db, user, storage):
    document, _ = service.upload_resume(
        db,
        user_id=user.id,
        filename="resume.pdf",
        declared_media_type="application/pdf",
        data=make_resume_pdf(),
        storage=storage,
    )
    return service.start_run(db, document=document)


def _reload(db, run):
    db.expire_all()
    return db.get(ResumeProcessingRun, run.id)


# --- identity ----------------------------------------------------------------


def test_worker_ids_are_unique_per_process_not_per_host():
    """Two replicas of one image must not be able to claim as the same worker."""
    assert lease.new_worker_id() != lease.new_worker_id()


# --- acquisition -------------------------------------------------------------


def test_acquiring_an_unleased_run_succeeds(db, run):
    assert lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)

    held = _reload(db, run)
    assert held.lease_owner == "worker-a"
    assert held.leased_until is not None
    assert held.heartbeat_at is not None


def test_a_second_worker_cannot_take_a_live_lease(db, run):
    """The requirement: worker A acquires, worker B cannot execute it."""
    assert lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    assert not lease.acquire(db, run_id=run.id, owner="worker-b", lease_seconds=60)

    # And the loser did not quietly overwrite the winner's claim.
    assert _reload(db, run).lease_owner == "worker-a"


def test_the_holder_may_reacquire_its_own_run(db, run):
    """A worker that crashed after claiming picks its own run back up rather
    than waiting out a lease it already owns."""
    assert lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    assert lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)


def test_an_expired_lease_can_be_taken_by_anyone(db, run):
    """Recovery after a worker dies: the claim conditions are evaluated against
    a moment, so an expired lease is simply not a lease."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert lease.acquire(
        db, run_id=run.id, owner="worker-a", lease_seconds=60, now=past
    )

    assert lease.acquire(db, run_id=run.id, owner="worker-b", lease_seconds=60)
    assert _reload(db, run).lease_owner == "worker-b"


def test_a_terminal_run_cannot_be_claimed(db, run):
    """Completed and failed runs are the record. Nothing may take them."""
    run.status = "failed"
    run.failed_stage = "validate"
    run.error_code = "malformed_pdf"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    assert not lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)


# --- renewal -----------------------------------------------------------------


def test_renewing_extends_the_lease(db, run):
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    first = _reload(db, run).leased_until

    later = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert lease.renew(
        db, run_id=run.id, owner="worker-a", lease_seconds=60, now=later
    )

    held = _reload(db, run)
    assert held.leased_until > first
    assert held.heartbeat_at is not None


def test_renewing_a_lease_someone_else_took_fails(db, run):
    """The signal a worker needs: it was reaped while it was still working."""
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60, now=past)
    lease.acquire(db, run_id=run.id, owner="worker-b", lease_seconds=60)

    assert not lease.renew(
        db, run_id=run.id, owner="worker-a", lease_seconds=60
    )


def test_renewing_a_finished_run_fails(db, run):
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    run.status = "failed"
    run.failed_stage = "validate"
    run.error_code = "malformed_pdf"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    assert not lease.renew(db, run_id=run.id, owner="worker-a", lease_seconds=60)


# --- release -----------------------------------------------------------------


def test_releasing_clears_the_lease_but_keeps_the_heartbeat(db, run):
    """When a run last showed a sign of life outlives the lease itself."""
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    assert lease.release(db, run_id=run.id, owner="worker-a")

    released = _reload(db, run)
    assert released.lease_owner is None
    assert released.leased_until is None
    assert released.heartbeat_at is not None


def test_releasing_a_lease_you_do_not_hold_changes_nothing(db, run):
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    assert not lease.release(db, run_id=run.id, owner="worker-b")
    assert _reload(db, run).lease_owner == "worker-a"


# --- reading the queue -------------------------------------------------------


def test_a_claimable_run_is_visible_until_someone_takes_it(db, run):
    assert run.id in lease.claimable_run_ids(db, limit=10)

    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60)
    assert run.id not in lease.claimable_run_ids(db, limit=10)


def test_expired_runs_are_distinguished_from_never_claimed_ones(db, run):
    """The reaper treats these differently, so the queries must not blur them."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=1)

    # Never claimed: no lease was ever taken.
    assert [r.id for r in lease.unclaimed_runs(db, limit=10, older_than=cutoff)] == [run.id]
    assert lease.expired_runs(db, limit=10) == []

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=60, now=past)

    # Now abandoned: a lease exists and has lapsed.
    assert [r.id for r in lease.expired_runs(db, limit=10)] == [run.id]
    assert lease.unclaimed_runs(db, limit=10, older_than=cutoff) == []
