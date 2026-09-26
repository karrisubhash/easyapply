"""Database errors carry no row values into logs, and none into a response.

Two exits a database error can leak through:

* the LOG. An exception's message is what a traceback prints, and SQLAlchemy
  renders the bound parameters into it — resume text, names, emails — unless
  the engine is built with hide_parameters. `app/db/session.py` builds every
  application engine that way; these tests hold it, with a positive control
  showing the same error from an engine without it really would have leaked.
* the RESPONSE. An integrity error nobody mapped, or any DataError, is re-raised
  on purpose (see app/api/errors.py) and becomes Starlette's own 500. That body
  is fixed text, never the exception's; pinned here so it stays that way.

What hide_parameters does NOT cover, and these tests do not claim: PostgreSQL's
own message can quote a value (`invalid input syntax for type integer: "…"`,
or a unique violation's `Key (email)=(…)` detail). That text is in the
exception whatever the engine does, so the log call sites decide what to print.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError

from app.api.deps import get_db, get_processing_queue, get_tailoring_queue
from app.api.identity import current_user
from app.db import session as app_session
from app.main import app

SECRET = "Jane Q. Candidate, 12 Private Lane — resume text that must not be logged"


def _too_long_for_the_email_column(engine) -> str:
    """A DataError whose PostgreSQL message does not itself quote the value.

    `value too long for type character varying(255)` names the type only, so
    whatever of SECRET appears in the exception came from SQLAlchemy's
    rendering of the parameters — exactly the part hide_parameters governs.
    """
    with engine.connect() as connection:
        try:
            connection.execute(
                text("INSERT INTO users (email) VALUES (:email)"),
                {"email": SECRET * 10},
            )
        except DataError as exc:
            return str(exc)
        finally:
            connection.rollback()
    pytest.fail("the insert was expected to fail with a DataError")


def test_the_application_engine_hides_parameters():
    """The engine the API and worker actually use. Built, never connected."""
    assert app_session.engine.hide_parameters is True
    assert app_session.SessionLocal.kw["bind"] is app_session.engine


def test_a_database_error_from_an_application_engine_carries_no_row_values(database_url):
    engine = app_session.build_engine(database_url)
    try:
        message = _too_long_for_the_email_column(engine)
    finally:
        engine.dispose()

    assert "value too long" in message, "not the error this test is about"
    assert SECRET not in message
    assert "hidden" in message


def test_without_hide_parameters_the_same_error_would_have_leaked(database_url):
    """The positive control: the test above is not green by accident."""
    engine = create_engine(database_url)
    try:
        message = _too_long_for_the_email_column(engine)
    finally:
        engine.dispose()

    assert SECRET in message


# --- the response ------------------------------------------------------------------


@pytest.fixture
def crashing_client(db, queue, tailoring_queue):
    """A client that answers a crash the way production does, instead of raising it.

    The ordinary `client` fixture re-raises server exceptions into the test,
    which is right for every other test and hides the body a user would get.
    """
    def override_get_db():
        try:
            yield db
        finally:
            db.rollback()

    overrides = {
        get_db: override_get_db,
        get_processing_queue: lambda: queue,
        get_tailoring_queue: lambda: tailoring_queue,
    }
    app.dependency_overrides.update(overrides)
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        for dependency in (*overrides, current_user):
            app.dependency_overrides.pop(dependency, None)


class _DriverError(Exception):
    """Shaped like a psycopg error nobody mapped: no SQLSTATE, no constraint."""


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError(f"INSERT INTO users VALUES ('{SECRET}')", {"email": SECRET}, _DriverError(SECRET)),
        DataError(f"INSERT INTO users VALUES ('{SECRET}')", {"email": SECRET}, _DriverError(SECRET)),
    ],
    ids=["unmapped-integrity-error", "data-error"],
)
def test_an_unmapped_database_error_is_a_500_that_says_nothing(crashing_client, error):
    def raise_it():
        raise error

    app.dependency_overrides[current_user] = raise_it
    response = crashing_client.get("/api/v1/profile")

    assert response.status_code == 500
    assert SECRET not in response.text
    assert "INSERT" not in response.text
    assert response.text == "Internal Server Error"
