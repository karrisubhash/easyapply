"""The development identity seam: where it refuses to run, and what it refuses to believe.

`app/api/dev_identity.py` trusts a header, so these tests hold the edges of that
trust: it does not operate at all in production; a header that is present but
unusable is a 401 rather than a new user or a 500; and two first sights of one
email racing each other both resolve to the one row the unique index allowed.

The race tests commit through their own connections — the savepoint session the
rest of the suite shares is one connection and cannot race itself — and delete
what they made in a `finally` block, the posture `test_job_concurrency.py` takes.
"""

import threading

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.api.dev_identity import EMAIL_MAX_LENGTH, current_user
from app.api.errors import UnauthenticatedError
from app.core.config import Settings, settings
from app.models import User

PROFILE = "/api/v1/profile"
DUMMY_DATABASE_URL = "postgresql+psycopg://u:p@localhost:5432/nowhere"


def user_count(db) -> int:
    return db.scalar(select(func.count()).select_from(User))


def assert_unauthenticated(response, *, not_containing: str | None = None) -> None:
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthenticated"
    assert isinstance(body["detail"], str)
    if not_containing:
        assert not_containing.lower() not in response.text.lower()


# --- production --------------------------------------------------------------


def test_production_refuses_every_request_whatever_the_header_says(client, db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    before = user_count(db)

    assert_unauthenticated(client.get(PROFILE))
    assert_unauthenticated(
        client.get(PROFILE, headers={"X-Dev-User-Email": "victim@example.com"}),
        not_containing="victim@example.com",
    )
    assert_unauthenticated(
        client.post(f"{PROFILE}/versions", headers={"X-Dev-User-Email": "victim@example.com"})
    )

    assert user_count(db) == before, "a refused request provisioned a user"


def test_production_refuses_before_the_database_is_touched(monkeypatch):
    """Not a lookup that happens to fail: the database is never asked."""

    class UntouchableSession:
        def __getattr__(self, name):
            raise AssertionError(f"the database was touched ({name}) in production")

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(UnauthenticatedError):
        current_user(db=UntouchableSession(), x_dev_user_email="dev@easyapply.local")


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_test_still_resolve_the_header(client, monkeypatch, environment):
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    response = client.get(PROFILE, headers={"X-Dev-User-Email": "someone@example.com"})
    # No profile yet is a 404 — which is an answer about a resolved user.
    assert response.status_code in (200, 404)


def test_the_environment_setting_is_a_closed_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DUMMY_DATABASE_URL)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert Settings(_env_file=None).ENVIRONMENT == "development"

    monkeypatch.setenv("ENVIRONMENT", "  Production ")
    assert Settings(_env_file=None).ENVIRONMENT == "production"

    # A typo must fail loudly at startup, never fall back to development.
    monkeypatch.setenv("ENVIRONMENT", "prod")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# --- unusable headers ----------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_a_blank_header_is_401_not_a_new_user(client, db, value):
    before = user_count(db)

    assert_unauthenticated(client.get(PROFILE, headers={"X-Dev-User-Email": value}))

    assert user_count(db) == before
    assert db.scalar(select(User).where(User.email == "")) is None


def test_an_overlong_header_is_401_not_a_500(client, db):
    overlong = "a" * (EMAIL_MAX_LENGTH - len("@example.com") + 1) + "@example.com"
    assert len(overlong) == EMAIL_MAX_LENGTH + 1
    before = user_count(db)

    assert_unauthenticated(
        client.get(PROFILE, headers={"X-Dev-User-Email": overlong}), not_containing=overlong
    )
    assert user_count(db) == before


def test_a_header_exactly_the_column_length_is_accepted(client, db):
    longest = "a" * (EMAIL_MAX_LENGTH - len("@example.com")) + "@example.com"
    assert len(longest) == EMAIL_MAX_LENGTH

    response = client.get(PROFILE, headers={"X-Dev-User-Email": longest})
    assert response.status_code in (200, 404)
    # Directly as well: the client fixture rolls back a request that did not
    # commit, so the row the GET provisioned is gone by now.
    assert current_user(db=db, x_dev_user_email=longest).email == longest


@pytest.mark.parametrize("value", ["a\x00b@example.com", "a\x01b@example.com", "a\x7fb@example.com"])
def test_control_characters_are_401_not_a_database_error(db, value):
    """Called directly: an HTTP client refuses to put most of these on the wire."""
    with pytest.raises(UnauthenticatedError):
        current_user(db=db, x_dev_user_email=value)


def test_an_absent_header_still_means_the_configured_development_user(client, db):
    response = client.get(PROFILE)
    assert response.status_code in (200, 404)
    assert current_user(db=db, x_dev_user_email=None).email == settings.DEV_USER_EMAIL.lower()


def test_a_blank_configured_development_user_is_refused_too(client, monkeypatch):
    monkeypatch.setattr(settings, "DEV_USER_EMAIL", "   ")
    assert_unauthenticated(client.get(PROFILE))


# --- concurrent first sight ------------------------------------------------------

RACE_EMAIL = "first-sight-race@example.com"


@pytest.fixture
def race_cleanup(engine):
    def purge():
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE email = :e"), {"e": RACE_EMAIL})

    purge()
    try:
        yield
    finally:
        purge()


def test_losing_a_first_sight_race_returns_the_winners_row(engine, race_cleanup, monkeypatch):
    """Deterministic: the winner commits between the loser's SELECT and INSERT.

    Before the fix the loser's INSERT hit ix_users_email and the request was a
    500. Now it rolls back only its savepoint, re-reads, and gets the winner's
    row — and its own transaction is still usable afterwards.
    """
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    loser, winner_session = factory(), factory()
    try:
        real_scalar = loser.scalar
        lookups = []

        def lookup_then_lose_the_race(statement, *args, **kwargs):
            result = real_scalar(statement, *args, **kwargs)
            if not lookups:
                lookups.append(result)
                winner_session.add(User(email=RACE_EMAIL))
                winner_session.commit()
            return result

        monkeypatch.setattr(loser, "scalar", lookup_then_lose_the_race)

        resolved = current_user(db=loser, x_dev_user_email=RACE_EMAIL)
        winner_id = winner_session.scalar(select(User.id).where(User.email == RACE_EMAIL))

        assert lookups == [None], "the loser's first lookup should have missed"
        assert resolved.id == winner_id
        # The request's transaction survived the lost INSERT.
        assert loser.execute(text("SELECT 1")).scalar_one() == 1
        loser.commit()
    finally:
        loser.close()
        winner_session.close()


def test_many_simultaneous_first_sights_resolve_to_one_row(engine, race_cleanup):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    workers = 8
    barrier = threading.Barrier(workers)
    resolved: list[int] = []
    errors: list[BaseException] = []

    def first_sight():
        db = factory()
        try:
            barrier.wait(timeout=10)
            user = current_user(db=db, x_dev_user_email=RACE_EMAIL)
            db.commit()
            resolved.append(user.id)
        except BaseException as exc:  # recorded, not swallowed: asserted below
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=first_sight) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(resolved) == workers
    assert len(set(resolved)) == 1

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT count(*) FROM users WHERE email = :e"), {"e": RACE_EMAIL}
        ).scalar_one()
    assert rows == 1
