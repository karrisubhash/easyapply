"""Concurrent ingestion of the same job, against real separate connections.

The shared savepoint session every other test in this suite runs on is one
connection inside a transaction nobody else can see — exactly the wrong
instrument for asking "what happens when two requests race," since it can only
prove one connection is consistent with itself. These tests commit, and clean
up after themselves in a `finally` block, the same posture
`test_resume_retry_concurrency.py` takes for the identical reason.
"""

import threading

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models import Job, User
from app.services.job.service import ingest_job
from app.schemas.job import JobIngestionRequest
from tests.job_fixtures import make_job_payload

EMAIL = "job-concurrency@example.com"


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
        session.close()


@pytest.fixture
def real_user(engine, sessions):
    db = sessions()

    def purge():
        with engine.begin() as connection:
            uid = connection.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": EMAIL}
            ).scalar()
            if uid is None:
                return
            connection.execute(
                text(
                    "DELETE FROM job_processing_runs WHERE job_id IN"
                    " (SELECT id FROM jobs WHERE user_id = :u)"
                ),
                {"u": uid},
            )
            connection.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": uid})
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})

    purge()
    user = User(email=EMAIL)
    db.add(user)
    db.commit()
    user_id = user.id

    try:
        yield user_id
    finally:
        purge()


def jobs_for(engine, user_id):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        return list(db.scalars(select(Job).where(Job.user_id == user_id)))
    finally:
        db.close()


def test_two_threads_ingesting_the_identical_job_produce_exactly_one_row(
    engine, sessions, real_user
):
    """The requirement: only one canonical job wins where the identity policy
    says the jobs are identical."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    payload = JobIngestionRequest.model_validate(
        make_job_payload(external_job_id="race-1")
    )

    results = []
    barrier = threading.Barrier(6)

    def race():
        db = factory()
        try:
            barrier.wait(timeout=10)
            job, created = ingest_job(db, user_id=real_user, payload=payload)
            results.append((job.id, created))
        finally:
            db.close()

    threads = [threading.Thread(target=race) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 6
    job_ids = {job_id for job_id, _ in results}
    assert len(job_ids) == 1, f"expected one canonical job, got {job_ids}"

    created_count = sum(1 for _, created in results if created)
    assert created_count == 1, "exactly one thread should have created the row"

    rows = jobs_for(engine, real_user)
    assert len(rows) == 1


def test_concurrent_ingestion_by_canonical_url_also_converges_to_one_row(
    engine, sessions, real_user
):
    """The tier-2 identity path (no external_job_id) is the same race with a
    different key — worth its own test since it is a different unique index."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    payload = JobIngestionRequest.model_validate(
        make_job_payload(
            external_job_id=None,
            source_url="https://www.linkedin.com/jobs/view/race-url?utm_source=a",
        )
    )

    results = []
    barrier = threading.Barrier(6)

    def race():
        db = factory()
        try:
            barrier.wait(timeout=10)
            job, created = ingest_job(db, user_id=real_user, payload=payload)
            results.append((job.id, created))
        finally:
            db.close()

    threads = [threading.Thread(target=race) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    job_ids = {job_id for job_id, _ in results}
    assert len(job_ids) == 1
    assert sum(1 for _, created in results if created) == 1


def test_concurrent_ingestion_of_genuinely_different_jobs_creates_both(
    engine, sessions, real_user
):
    """The race protection must not become an accidental global lock — two
    real, distinct jobs ingested at the same instant must both survive."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    payload_a = JobIngestionRequest.model_validate(
        make_job_payload(external_job_id="distinct-a")
    )
    payload_b = JobIngestionRequest.model_validate(
        make_job_payload(external_job_id="distinct-b")
    )

    results = []
    barrier = threading.Barrier(2)

    def race(payload):
        db = factory()
        try:
            barrier.wait(timeout=10)
            job, created = ingest_job(db, user_id=real_user, payload=payload)
            results.append((job.id, created))
        finally:
            db.close()

    threads = [
        threading.Thread(target=race, args=(payload_a,)),
        threading.Thread(target=race, args=(payload_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert all(created for _, created in results)
    assert results[0][0] != results[1][0]
    assert len(jobs_for(engine, real_user)) == 2


def test_a_later_sequential_ingestion_after_the_race_still_deduplicates(
    engine, sessions, real_user
):
    """After the dust settles, the identity index keeps doing its ordinary
    job — the concurrency guard is not a one-shot special case."""
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    payload = JobIngestionRequest.model_validate(
        make_job_payload(external_job_id="race-then-sequential")
    )

    db1 = factory()
    job1, created1 = ingest_job(db1, user_id=real_user, payload=payload)
    db1.close()

    db2 = factory()
    job2, created2 = ingest_job(db2, user_id=real_user, payload=payload)
    db2.close()

    assert created1 is True
    assert created2 is False
    assert job1.id == job2.id
