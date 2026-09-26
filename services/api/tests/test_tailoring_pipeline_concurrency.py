"""A stalled run against a worker that is still alive, on real separate connections.

The savepoint session the rest of the suite runs on is one connection inside a
transaction nobody else can see, so it cannot hold a row lock against itself.
What `service._abandon_if_unattended` relies on is a lock held by ANOTHER
connection — the worker's — so these tests commit real rows and clean up after
themselves in a `finally`, the posture `test_matching_concurrency.py` takes for
the same reason.

What is being proved (H1): "stalled" is a clock reading, and a clock reading is
not allowed to take a run away from a worker that is still working on it. A
worker holds the run's row lock from its first write until the run is terminal;
while it does, a Generate press gets that run back as in flight. Once the lock
is gone — the worker's connection closed, its transaction rolled back — the same
press abandons the run and opens a new attempt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update
from sqlalchemy.orm import sessionmaker

from app.models import User
from app.models.tailoring import TailoringRun
from app.services.tailoring import service
from tests.tailoring_fixtures import a_backend_job, a_tailorable_version

EMAIL = "tailoring-concurrency@example.com"

#: Every table a tailorable version, a structured job and a run touch, children
#: first. Deleted explicitly: `session_replication_role = replica` switches the
#: immutability triggers off, and with them the triggers that implement FOREIGN
#: KEY cascades, so nothing is removed that is not named here.
_PURGE = (
    "DELETE FROM tailoring_issues WHERE tailored_resume_id IN (SELECT id FROM tailored_resumes"
    " WHERE tailoring_run_id IN (SELECT id FROM tailoring_runs WHERE user_id = :u))",
    "DELETE FROM tailored_resume_artifacts WHERE tailored_resume_id IN (SELECT id FROM"
    " tailored_resumes WHERE tailoring_run_id IN (SELECT id FROM tailoring_runs WHERE user_id = :u))",
    "DELETE FROM tailored_resumes WHERE tailoring_run_id IN"
    " (SELECT id FROM tailoring_runs WHERE user_id = :u)",
    "DELETE FROM tailoring_runs WHERE user_id = :u",
    "DELETE FROM job_requirements WHERE job_processing_run_id IN (SELECT id FROM"
    " job_processing_runs WHERE job_id IN (SELECT id FROM jobs WHERE user_id = :u))",
    "DELETE FROM job_processing_runs WHERE job_id IN (SELECT id FROM jobs WHERE user_id = :u)",
    "DELETE FROM jobs WHERE user_id = :u",
    "DELETE FROM project_technologies WHERE project_id IN (SELECT id FROM projects WHERE"
    " profile_version_id IN (SELECT pv.id FROM profile_versions pv JOIN profiles p"
    " ON p.id = pv.profile_id WHERE p.user_id = :u))",
    *(
        f"DELETE FROM {table} WHERE profile_version_id IN (SELECT pv.id FROM profile_versions pv"
        " JOIN profiles p ON p.id = pv.profile_id WHERE p.user_id = :u)"
        for table in (
            "projects",
            "experiences",
            "education",
            "skills",
            "certifications",
            "profile_links",
        )
    ),
    "DELETE FROM profile_versions WHERE profile_id IN (SELECT id FROM profiles WHERE user_id = :u)",
    "DELETE FROM profiles WHERE user_id = :u",
    "DELETE FROM users WHERE id = :u",
)


@pytest.fixture
def sessions(engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    made = []

    def _make():
        session = factory()
        made.append(session)
        return session

    yield _make

    for session in made:
        session.rollback()
        session.close()


@pytest.fixture
def committed(engine, sessions):
    """A committed user, confirmed version and structured job, removed afterwards."""

    def purge():
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL session_replication_role = 'replica'"))
            uid = connection.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": EMAIL}
            ).scalar()
            if uid is None:
                return
            for statement in _PURGE:
                connection.execute(text(statement), {"u": uid})

    purge()
    db = sessions()
    account = User(email=EMAIL)
    db.add(account)
    db.commit()
    a_tailorable_version(db, account)
    job, _ = a_backend_job(db, account, external_job_id="tailor-concurrency")
    try:
        yield account.id, job.id
    finally:
        purge()


def _lost_run(db, *, user_id: int, job_id: int) -> int:
    """A queued run nobody enqueued, aged past the stall threshold."""
    run, created = service.start_tailoring(db, user_id=user_id, job_id=job_id)
    assert created
    db.execute(
        update(TailoringRun)
        .where(TailoringRun.id == run.id)
        .values(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    )
    db.commit()
    return run.id


def test_a_stalled_run_whose_worker_still_holds_it_stays_in_flight(committed, sessions):
    user_id, job_id = committed
    request = sessions()
    run_id = _lost_run(request, user_id=user_id, job_id=job_id)

    # The worker's first write, exactly as `pipeline.process_run` makes it,
    # left uncommitted: the row lock is what a live worker holds for the whole
    # of its work, provider call included.
    worker = sessions()
    worker.execute(
        update(TailoringRun)
        .where(TailoringRun.id == run_id)
        .values(started_at=datetime.now(timezone.utc))
    )

    press = sessions()
    # SKIP LOCKED must not wait; if it ever did, this fails instead of hanging.
    press.execute(text("SET lock_timeout = '5s'"))
    try:
        run, created = service.start_tailoring(press, user_id=user_id, job_id=job_id)
    finally:
        press.execute(text("SET lock_timeout = 0"))

    assert created is False
    assert run.id == run_id
    assert run.status == "queued"

    # The worker dies: its connection's transaction is rolled back and the lock
    # goes with it. Now nothing is working on the run, and the next press may
    # take it.
    worker.rollback()
    worker.close()

    again = sessions()
    fresh, created = service.start_tailoring(again, user_id=user_id, job_id=job_id)

    assert created is True
    assert fresh.id != run_id
    old = again.get(TailoringRun, run_id)
    again.refresh(old)
    assert (old.status, old.error_code) == ("failed", "worker_abandoned")
