"""Revision a7c3e5f19b20 (stored objects), against a real PostgreSQL.

In a throwaway database of its own, like test_migration_user_identities.py:

  * upgrade adds the table and changes nothing else; downgrade round-trips
    while it is empty;
  * downgrade REFUSES, leaving the schema at this revision, once it holds an
    object — those bytes are documents other rows still reference by key;
  * the size check is the database's;
  * the migrated table matches the model (no drift).
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

BEFORE = "f1a9d3c5b7e2"
THIS = "a7c3e5f19b20"


@pytest.fixture
def scratch_url(database_url):
    url = make_url(database_url)
    scratch = url.set(database=f"{url.database}_migrations_objects")
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


def test_upgrade_adds_the_table_and_an_empty_one_downgrades(scratch_url):
    config = alembic_config(scratch_url)
    command.upgrade(config, BEFORE)
    engine = create_engine(scratch_url)
    try:
        tables_before = set(inspect(engine).get_table_names())

        command.upgrade(config, THIS)
        assert current_revision(engine) == THIS
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == tables_before | {"stored_objects"}
        assert inspector.get_pk_constraint("stored_objects")["constrained_columns"] == ["key"]

        command.downgrade(config, BEFORE)
        assert current_revision(engine) == BEFORE
        assert "stored_objects" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_downgrade_refuses_while_any_object_is_held(scratch_url):
    config = alembic_config(scratch_url)
    command.upgrade(config, THIS)
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO stored_objects (key, content_type, data, size_bytes) "
                    "VALUES ('resumes/1/abc.pdf', 'application/pdf', '\\x0102'::bytea, 2)"
                )
            )
        with pytest.raises(RuntimeError, match="stored_objects holds 1 object"):
            command.downgrade(config, BEFORE)
        assert current_revision(engine) == THIS
        assert "stored_objects" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_the_recorded_size_must_be_the_datas(scratch_url):
    command.upgrade(alembic_config(scratch_url), THIS)
    engine = create_engine(scratch_url)
    try:
        for values, constraint in (
            ("('resumes/1/a.pdf', 'application/pdf', '\\x01'::bytea, 5)", "ck_stored_objects_size_matches"),
            ("('', 'application/pdf', '\\x01'::bytea, 1)", "ck_stored_objects_key_non_empty"),
        ):
            with pytest.raises(IntegrityError) as error, engine.begin() as connection:
                connection.execute(
                    text(f"INSERT INTO stored_objects (key, content_type, data, size_bytes) VALUES {values}")
                )
            assert error.value.orig.diag.constraint_name == constraint
    finally:
        engine.dispose()


def test_the_migrated_table_matches_the_model(scratch_url):
    command.upgrade(alembic_config(scratch_url), "head")
    engine = create_engine(scratch_url)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert [difference for difference in differences if "stored_objects" in repr(difference)] == []
    finally:
        engine.dispose()
