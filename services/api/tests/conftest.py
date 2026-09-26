"""Test harness for the profile domain.

Almost everything worth testing here — check constraints, partial unique
indexes, expression indexes, the immutability triggers — exists only in the
database. So these tests run against a real PostgreSQL database built by the
Alembic migrations, not by Base.metadata.create_all(), which knows nothing about
the triggers and would quietly pass tests that production would fail.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db, get_processing_queue, get_tailoring_queue
from app.core.config import settings
from app.main import app
from app.models import Profile, ProfileVersion, User

API_ROOT = Path(__file__).resolve().parents[1]

#: Stamped on every connection this pytest session opens to the test database.
#:
#: Postgres reports it in `pg_stat_activity`, which is what lets the session
#: teardown below close this process's own leftovers and nobody else's. The pid
#: is in it so that two sessions — a developer and a CI container, or two
#: terminals — can never mistake each other's connections for their own.
_APPLICATION_NAME = f"easyapply-pytest-{os.getpid()}"


def _close_leftover_connections(url) -> int:
    """Close anything this session still has open to the test database.

    `Engine.dispose()` closes what is in the POOL. It cannot close a connection
    that is still checked out, and a test that stops waiting on a thread —
    `thread.join(timeout=...)` returning while the thread is alive, which the
    concurrency suites do on purpose so a deadlock fails instead of hanging —
    leaves exactly that: a session nobody will close, usually sitting `idle in
    transaction`, outliving the process that opened it.

    It stayed invisible because the next session's `DROP DATABASE … WITH
    (FORCE)` cleared it. What FORCE also does is terminate the connections of
    any pytest session running at that moment, which surfaces as `AdminShutdown`
    in whichever unrelated test was unlucky — a red suite that says nothing
    about the code. Cleaning up after ourselves here is what stops a dead run
    from being charged to a live one.

    Filtered on the test database AND on `application_name`, so it closes only
    what this process opened, and could not reach the development database even
    if both other filters were somehow wrong.

    The CTE is `MATERIALIZED` deliberately. A WHERE clause gives no guarantee
    about the order its conjuncts are evaluated in, so
    `WHERE application_name = :name AND pg_terminate_backend(pid)` is free to
    run the termination first — against every row in `pg_stat_activity`,
    including this connection and every other database's. `MATERIALIZED` is a
    real optimisation barrier: the victims are chosen, in full, before anything
    is terminated.
    """
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            return connection.execute(
                text(
                    "WITH victims AS MATERIALIZED ("
                    "  SELECT pid FROM pg_stat_activity"
                    "   WHERE datname = :database"
                    "     AND application_name = :name"
                    "     AND pid <> pg_backend_pid()"
                    ")"
                    " SELECT count(pg_terminate_backend(pid)) FROM victims"
                ),
                {"database": url.database, "name": _APPLICATION_NAME},
            ).scalar_one()
    finally:
        admin.dispose()


def _test_database_url() -> str:
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    url = make_url(settings.DATABASE_URL)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if make_url(url).database == make_url(settings.DATABASE_URL).database:
        pytest.fail(
            "TEST_DATABASE_URL points at the development database; "
            "these tests drop and recreate whatever they are given."
        )
    return url


@pytest.fixture(scope="session")
def engine(database_url):
    """A freshly migrated database for the whole session."""
    url = make_url(database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    # Set before env.py runs, so the migration cannot reach the developer's
    # own database.
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    # Tagged so the teardown can identify this session's own connections.
    test_engine = create_engine(
        database_url,
        connect_args={"application_name": _APPLICATION_NAME},
    )
    try:
        yield test_engine
    finally:
        test_engine.dispose()
        _close_leftover_connections(url)


@pytest.fixture
def db(engine):
    """A session whose work is thrown away after each test.

    The session runs inside a savepoint on an outer transaction that is always
    rolled back, so tests can commit freely and still leave nothing behind.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True, scope="session")
def _no_background_processing():
    """Keep the test process free of the application's own threads.

    The app starts a worker pool and a reaper on startup, and TestClient runs
    the lifespan. Left alone, every test would race a pool that is holding a
    different session against a database the test is about to roll back. Tests
    drive the worker deliberately instead — which is also the only way to assert
    that the API returned *before* the pipeline ran.
    """
    from app.core.config import settings

    settings.RESUME_QUEUE_BACKEND = "external"
    settings.RESUME_REAPER_ENABLED = False
    # Inline rather than external: building the inline queue starts no threads,
    # and every test that touches tailoring overrides the dependency anyway.
    settings.TAILORING_QUEUE_BACKEND = "inline"
    yield


#: The operator token the test process runs with. Not a secret — it is in the
#: repository — and deliberately not anything a developer would configure, so a
#: test that passes cannot be passing on the developer's real token.
OPERATOR_TEST_TOKEN = "easyapply-test-operator-token"


@pytest.fixture(autouse=True, scope="session")
def _configuration_pinned_against_the_developer_env():
    """Pin every setting whose developer value would change what the suite proves.

    `settings` is built once, at import, from whatever `.env` sits in the working
    directory — a developer's own. Left alone, that file decides what the suite
    means: a developer token turns every operator test into a 403, a developer
    AI flag makes the suite call a paid provider, and the suite would depend on
    secrets it has no business reading. Tests that exercise a setting set it
    themselves; monkeypatch restores to this.

    Only the test process's copy is edited, after loading. How configuration is
    loaded — and what production does with it — is untouched, which
    `test_operator_test_isolation.py` holds.

    Plain assignment, like the fixture above, and deliberately not monkeypatch:
    if a field is ever renamed, `MonkeyPatch.setattr` fails with `repr(settings)`
    in its message — every secret the developer's `.env` holds, in clear, as the
    setup error of every test. Pydantic's own error names only the field.
    """
    # The operator boundary fails closed without a token, so the suite runs with
    # a known, public one; `test_operator_api.py` sends it. A developer's own
    # token never reaches a test.
    settings.OPERATOR_API_ENABLED = True
    settings.OPERATOR_API_TOKEN = OPERATOR_TEST_TOKEN

    # A developer `.env` saying production would make every API test a 401 —
    # the development identity refuses to run there. The suite says what it is.
    settings.ENVIRONMENT = "test"

    # The suite's identity is the development header, whatever a developer's
    # `.env` says; the Entra path's tests (test_identity_entra.py) set their own
    # mode, secret and tenant, and monkeypatch restores to this.
    settings.AUTH_MODE = "development"
    settings.IDENTITY_ASSERTION_SECRET = ""
    settings.IDENTITY_ASSERTION_SECRET_PREVIOUS = ""
    settings.ENTRA_TENANT_ID = ""
    settings.ENTRA_ISSUER = ""

    # No paid provider, ever, from a developer's configuration. A `.env` with
    # an AI flag on and a key beside it would otherwise have the suite send
    # resumes to a model and bill for it. Every switch off, the rollout list
    # empty and every key blank — the named ones AND anything matching the
    # pattern, so a switch added later is pinned without anyone remembering.
    # A test that genuinely needs one on sets it itself, with a fake provider.
    for name in (*AI_SWITCHES, *_matching_fields("_AI_ENABLED")):
        setattr(settings, name, False)
    settings.TAILORING_AI_USER_IDS = ""
    for name in (*PROVIDER_KEYS, *_matching_fields("_API_KEY")):
        setattr(settings, name, None)

    # And out of the process environment too. The adapters read the key from
    # `settings`, but a vendor SDK falls back to its own environment variable
    # when handed none, and every child process a test starts inherits this
    # environment. Restored at the end of the session.
    scrubbed = {
        name: os.environ.pop(name) for name in list(os.environ) if _is_provider_secret(name)
    }
    try:
        yield
    finally:
        os.environ.update(scrubbed)


#: The AI switches, by name. Also swept by suffix; named so that a rename fails
#: `test_security_test_isolation.py` instead of silently shrinking the sweep.
AI_SWITCHES = ("RESUME_AI_ENABLED", "JOB_AI_ENABLED", "TAILORING_AI_ENABLED")
PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")


def _matching_fields(suffix: str) -> tuple[str, ...]:
    return tuple(name for name in type(settings).model_fields if name.endswith(suffix))


def _is_provider_secret(name: str) -> bool:
    upper = name.upper()
    # ANTHROPIC_AUTH_TOKEN is the SDK's other credential variable.
    return upper.endswith("_API_KEY") or upper == "ANTHROPIC_AUTH_TOKEN"


class CollectingQueue:
    """Records what was enqueued and runs it only when asked.

    Stands in for the thread pool so a test can assert what the API queued, then
    execute it and assert what the worker did — two facts that are one
    indistinguishable event under a real pool.
    """

    def __init__(self, worker):
        self.worker = worker
        self.enqueued: list[int] = []
        self._pending: list[int] = []

    def enqueue(self, run_id: int) -> bool:
        if run_id in self._pending:
            return False
        self.enqueued.append(run_id)
        self._pending.append(run_id)
        return True

    def drain(self, *, max_passes: int = 10) -> list:
        """Run everything queued, including retries queued by the running.

        Bounded, because a retry policy bug that queues forever should fail a
        test rather than hang one.
        """
        outcomes = []
        for _ in range(max_passes):
            if not self._pending:
                break
            batch, self._pending = self._pending, []
            outcomes.extend(self.worker.execute(run_id) for run_id in batch)
        return outcomes

    def shutdown(self, *, wait: bool = True) -> None:
        return None


@pytest.fixture
def immediate_retries(monkeypatch):
    """Switch the backoff off for tests that are not about the backoff.

    A retry scheduled 30 seconds out is correct and is exactly what most tests
    do not want to wait for or reason about. Zeroing the base delay makes
    next_retry_at NULL — "nothing is holding this back" — so those tests keep
    asserting what they were written to assert.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "RESUME_RETRY_BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "RESUME_RETRY_BACKOFF_MAX_SECONDS", 0.0)
    return settings


@pytest.fixture
def worker(db):
    """A worker bound to this test's session.

    Heartbeat off: it would need its own connection, and this test's data lives
    in an uncommitted transaction on one. Renewal is covered directly in
    test_resume_lease.py instead, against the same session that owns the row.
    """
    from app.services.resume.worker import ResumeWorker

    @contextmanager
    def scope():
        yield db

    return ResumeWorker(
        session_scope=scope,
        worker_id="test-worker",
        heartbeat_seconds=0,
    )


@pytest.fixture
def queue(worker):
    collecting = CollectingQueue(worker)
    worker.queue = collecting
    return collecting


@pytest.fixture
def tailoring_worker(db):
    """A tailoring worker bound to this test's session.

    The scope yields the test's own session and does NOT close it — the data
    under test lives in an uncommitted transaction on that connection, and a
    worker that closed it would take the test's fixtures with it.
    """
    from app.services.tailoring.worker import TailoringWorker

    @contextmanager
    def scope():
        yield db

    return TailoringWorker(session_scope=scope)


@pytest.fixture
def tailoring_queue(tailoring_worker):
    """Records what the API queued, and runs it only when asked.

    The same CollectingQueue the resume domain uses, for the same reason: "the
    API returned" and "the work ran" are two facts that a real pool makes one
    indistinguishable event.
    """
    collecting = CollectingQueue(tailoring_worker)
    tailoring_worker.queue = collecting
    return collecting


@pytest.fixture
def client(db, queue, tailoring_queue):
    """A TestClient whose handlers run inside this test's savepoint.

    Only get_db is overridden. Dev identity, the service layer, the handlers
    and the error mapping all run exactly as shipped, so the commit path is
    genuinely exercised: the session joins the outer transaction with
    join_transaction_mode="create_savepoint", so a real commit releases a
    savepoint and the outer rollback still throws the test away.

    The rollback in the finally block is load-bearing. Without it a request
    that ends in an IntegrityError leaves the Session poisoned, and the NEXT
    request in the same test dies with PendingRollbackError instead of the
    status code under test.
    """

    def override_get_db():
        try:
            yield db
        finally:
            db.rollback()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_processing_queue] = lambda: queue
    app.dependency_overrides[get_tailoring_queue] = lambda: tailoring_queue
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(db):
    account = User(email="candidate@example.com")
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def profile(db, user):
    career_profile = Profile(user_id=user.id)
    db.add(career_profile)
    db.flush()
    return career_profile


@pytest.fixture
def make_version(db, profile, user):
    """Create a profile version, draft by default."""

    def _make(
        version_number=1,
        status="draft",
        is_current=False,
        target=None,
        flush=True,
        **kwargs,
    ):
        version = ProfileVersion(
            profile_id=(target or profile).id,
            version_number=version_number,
            status=status,
            is_current=is_current,
            created_by_type=kwargs.pop("created_by_type", "user"),
            created_by_user_id=kwargs.pop("created_by_user_id", user.id),
            confirmed_at=(
                datetime.now(timezone.utc) if status == "confirmed" else None
            ),
            **kwargs,
        )
        db.add(version)
        if flush:
            db.flush()
        return version

    return _make


@pytest.fixture
def confirm(db):
    """Confirm a draft version, the way the application is expected to.

    Build the version as a draft, fill it, then confirm: the triggers refuse
    snapshot rows written to an already-confirmed version.
    """

    def _confirm(version, make_current=False):
        version.status = "confirmed"
        version.confirmed_at = datetime.now(timezone.utc)
        db.flush()
        if make_current:
            version.is_current = True
            db.flush()
        return version

    return _confirm
