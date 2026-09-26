"""Two requests for the same match, racing, against real separate connections.

The savepoint session the rest of the suite runs on is one connection inside a
transaction nobody else can see — it can prove a connection is consistent with
itself and nothing more. §18's claim is about two connections, so these tests
commit and clean up after themselves in a `finally`, the same posture
`test_job_concurrency.py` takes for the identical reason.

What is being proved: `uq_match_results_version_requirements_algorithm` is the
arbiter, and the thread that loses the INSERT reads the winner's row instead of
raising. A duplicate match is not an error — it is the same answer.
"""

import threading
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    Job,
    JobProcessingRun,
    JobRequirements,
    MatchResult,
    Profile,
    ProfileVersion,
    Skill,
    User,
)
from app.schemas.job_candidate import JOB_CANDIDATE_SCHEMA_VERSION
from app.services.matching.service import create_match
from tests.matching_fixtures import AS_OF, a_candidate

EMAIL = "match-concurrency@example.com"


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
def raced_fixture(engine, sessions):
    """A committed user, confirmed profile version and structured job.

    Committed on purpose: the racing threads each open their own connection,
    so anything held in an uncommitted transaction would be invisible to them.
    """
    db = sessions()

    def purge():
        with engine.begin() as connection:
            # A CONFIRMED profile version cannot be deleted, and its snapshot
            # rows cannot be touched — that is the point of the triggers in
            # `10f47a8774be`, and the reason this teardown has to say so out
            # loud rather than quietly work. `session_replication_role` turns
            # user triggers off for this connection only, for the length of
            # this transaction, so the fixture can remove the very rows the
            # application is forbidden to. Nothing outside teardown may do it.
            connection.execute(text("SET LOCAL session_replication_role = 'replica'"))
            uid = connection.execute(
                text("SELECT id FROM users WHERE email = :e"), {"e": EMAIL}
            ).scalar()
            if uid is None:
                return
            connection.execute(
                text("DELETE FROM match_results WHERE user_id = :u"), {"u": uid}
            )
            connection.execute(
                text(
                    "DELETE FROM job_requirements WHERE job_processing_run_id IN"
                    " (SELECT id FROM job_processing_runs WHERE job_id IN"
                    "  (SELECT id FROM jobs WHERE user_id = :u))"
                ),
                {"u": uid},
            )
            connection.execute(
                text(
                    "DELETE FROM job_processing_runs WHERE job_id IN"
                    " (SELECT id FROM jobs WHERE user_id = :u)"
                ),
                {"u": uid},
            )
            connection.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": uid})
            connection.execute(
                text(
                    "DELETE FROM skills WHERE profile_version_id IN"
                    " (SELECT pv.id FROM profile_versions pv JOIN profiles p"
                    "  ON p.id = pv.profile_id WHERE p.user_id = :u)"
                ),
                {"u": uid},
            )
            connection.execute(
                text(
                    "DELETE FROM profile_versions WHERE profile_id IN"
                    " (SELECT id FROM profiles WHERE user_id = :u)"
                ),
                {"u": uid},
            )
            connection.execute(text("DELETE FROM profiles WHERE user_id = :u"), {"u": uid})
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})

    purge()

    user = User(email=EMAIL)
    db.add(user)
    db.flush()

    profile = Profile(user_id=user.id)
    db.add(profile)
    db.flush()

    version = ProfileVersion(
        profile_id=profile.id,
        version_number=1,
        status="draft",
        created_by_type="user",
        created_by_user_id=user.id,
    )
    db.add(version)
    db.flush()
    db.add(Skill(profile_version_id=version.id, name="Python"))
    db.flush()
    version.status = "confirmed"
    version.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    version.is_current = True
    db.flush()

    job = Job(
        user_id=user.id,
        source_type="linkedin",
        source_url="https://www.linkedin.com/jobs/view/match-race",
        canonical_url="https://www.linkedin.com/jobs/view/match-race",
        external_job_id="match-race",
        content_hash="d" * 64,
        job_title="Senior Backend Engineer",
        company_name="Acme Corp",
        description_text="Requirements:\n- Python\n",
        raw_payload={},
    )
    db.add(job)
    db.flush()

    run = JobProcessingRun(
        job_id=job.id,
        attempt=1,
        status="completed",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    requirements = JobRequirements(
        job_processing_run_id=run.id,
        structurer="deterministic",
        schema_version=JOB_CANDIDATE_SCHEMA_VERSION,
        payload=a_candidate(
            required_skills=["Python", "Rust"], preferred_skills=["Go"]
        ).model_dump(mode="json"),
    )
    db.add(requirements)
    db.commit()

    ids = (user.id, job.id, version.id)
    try:
        yield ids
    finally:
        purge()


def matches_for(engine, user_id):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        return list(db.scalars(select(MatchResult).where(MatchResult.user_id == user_id)))
    finally:
        db.close()


def race_create(engine, ids, *, threads=6):
    user_id, job_id, version_id = ids
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    results = []
    failures = []
    barrier = threading.Barrier(threads)

    def attempt():
        db = factory()
        try:
            barrier.wait(timeout=10)
            match, created = create_match(
                db,
                user_id=user_id,
                job_id=job_id,
                profile_version_id=version_id,
                as_of=AS_OF,
            )
            results.append((match.id, created))
        except Exception as error:  # recorded, not swallowed — see the asserts
            failures.append(error)
        finally:
            db.close()

    workers = [threading.Thread(target=attempt) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    return results, failures


def test_concurrent_requests_for_the_same_match_produce_exactly_one_row(
    engine, raced_fixture
):
    """§18. Six threads, one evaluation: the unique constraint decides and
    everyone else reads the winner."""
    results, failures = race_create(engine, raced_fixture)

    assert failures == [], f"a losing thread raised instead of reading the winner: {failures}"
    assert len(results) == 6

    match_ids = {match_id for match_id, _ in results}
    assert len(match_ids) == 1, f"expected one match, got {match_ids}"

    created_count = sum(1 for _, created in results if created)
    assert created_count == 1, "exactly one thread should have created the row"

    assert len(matches_for(engine, raced_fixture[0])) == 1


def test_a_losing_thread_returns_the_same_verdicts_as_the_winner(engine, raced_fixture):
    """Reading the winner's row has to mean reading a COMPLETE row — a
    half-written result would be worse than a duplicate."""
    results, failures = race_create(engine, raced_fixture)
    assert failures == []

    user_id = raced_fixture[0]
    stored = matches_for(engine, user_id)
    assert len(stored) == 1

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        match = db.get(MatchResult, stored[0].id)
        assert match.requirement_results
        assert (
            match.required_matched + match.required_missing + match.required_uncertain
            == match.required_total
        )
        for result in match.requirement_results:
            assert result.explanation
    finally:
        db.close()


def test_a_second_race_after_the_match_exists_creates_nothing(engine, raced_fixture):
    """The steady state: once the row is there, every request is a read."""
    race_create(engine, raced_fixture)
    results, failures = race_create(engine, raced_fixture)

    assert failures == []
    assert all(created is False for _, created in results)
    assert len(matches_for(engine, raced_fixture[0])) == 1
