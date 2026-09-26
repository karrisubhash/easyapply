"""Revision f1a9d3c5b7e2 (external user identities), against a real PostgreSQL.

In a throwaway database of its own — never the suite's shared one, which the
other tests hold at head — so it can move the schema up and down freely:

  * upgrading keeps every existing development user and what they own;
  * the mapping's uniqueness and non-empty checks are the database's;
  * downgrading round-trips while no user lacks an email, and REFUSES (leaving
    the schema at head) once someone exists only through an external identity;
  * the migrated tables match the models (no drift).
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from tests.conftest import API_ROOT

BEFORE = "d4a7c2e9b815"
THIS = "f1a9d3c5b7e2"


@pytest.fixture
def scratch_url(database_url):
    url = make_url(database_url)
    scratch = url.set(database=f"{url.database}_migrations")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{scratch.database}"'))
    try:
        yield scratch.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{scratch.database}" WITH (FORCE)'))
        admin.dispose()


def alembic_config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def current_revision(engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def email_nullable(engine) -> bool:
    return next(column for column in inspect(engine).get_columns("users") if column["name"] == "email")["nullable"]


def test_upgrade_keeps_development_users_and_downgrade_round_trips(scratch_url):
    config = alembic_config(scratch_url)
    command.upgrade(config, BEFORE)
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as connection:
            user_id = connection.execute(text("INSERT INTO users (email) VALUES ('kept@example.test') RETURNING id")).scalar_one()
            connection.execute(text("INSERT INTO profiles (user_id) VALUES (:u)"), {"u": user_id})

        command.upgrade(config, THIS)
        assert current_revision(engine) == THIS
        assert email_nullable(engine) is True
        with engine.connect() as connection:
            assert connection.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id}).scalar_one() == "kept@example.test"
            assert connection.execute(text("SELECT count(*) FROM profiles WHERE user_id = :u"), {"u": user_id}).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM user_identities")).scalar_one() == 0, "nobody is linked by email"

        command.downgrade(config, BEFORE)
        assert current_revision(engine) == BEFORE
        assert "user_identities" not in inspect(engine).get_table_names()
        assert email_nullable(engine) is False

        command.upgrade(config, THIS)
        assert current_revision(engine) == THIS
    finally:
        engine.dispose()


def test_the_mapping_is_unique_and_never_empty(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as connection:
            first = connection.execute(text("INSERT INTO users (email) VALUES (NULL) RETURNING id")).scalar_one()
            second = connection.execute(text("INSERT INTO users (email) VALUES (NULL) RETURNING id")).scalar_one()
            connection.execute(
                text("INSERT INTO user_identities (user_id, issuer, subject) VALUES (:u, 'https://i', 'oid-1')"), {"u": first}
            )
        # Several users without an email do not collide under the unique index.
        assert first != second
        for statement, params, constraint in (
            ("INSERT INTO user_identities (user_id, issuer, subject) VALUES (:u, 'https://i', 'oid-1')", {"u": second}, "uq_user_identities_issuer_subject"),
            ("INSERT INTO user_identities (user_id, issuer, subject) VALUES (:u, '', 'oid-2')", {"u": second}, "ck_user_identities_non_empty"),
            ("INSERT INTO user_identities (user_id, issuer, subject) VALUES (999999, 'https://i', 'oid-3')", {}, "fk_user_identities_user_id"),
        ):
            with pytest.raises(IntegrityError) as error, engine.begin() as connection:
                connection.execute(text(statement), params)
            assert error.value.orig.diag.constraint_name == constraint
        # The same subject under another issuer is another identity.
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO user_identities (user_id, issuer, subject) VALUES (:u, 'https://other', 'oid-1')"), {"u": second}
            )
        # A user who signs in cannot be deleted from under their mapping.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": first})
    finally:
        engine.dispose()


def test_downgrade_refuses_while_someone_exists_only_through_an_external_identity(scratch_url):
    config = alembic_config(scratch_url)
    command.upgrade(config, THIS)
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as connection:
            user = connection.execute(text("INSERT INTO users (email) VALUES (NULL) RETURNING id")).scalar_one()
            connection.execute(
                text("INSERT INTO user_identities (user_id, issuer, subject) VALUES (:u, 'https://i', 'oid-9')"), {"u": user}
            )
        with pytest.raises(RuntimeError, match="provisioned from an external identity"):
            command.downgrade(config, BEFORE)
        # Refused before anything changed: still at this revision, table intact.
        assert current_revision(engine) == THIS
        assert "user_identities" in inspect(engine).get_table_names()
        assert email_nullable(engine) is True
    finally:
        engine.dispose()


def test_the_migrated_tables_match_the_models(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        ours = [difference for difference in differences if "users" in repr(difference) or "user_identities" in repr(difference)]
        assert ours == []
    finally:
        engine.dispose()
