"""DatabaseStorage: the storage contract, kept in the application's own database.

The backend a deployment with no persistent disk uses (STORAGE_BACKEND=database),
so it must keep exactly the promises LocalFilesystemStorage keeps: an object
comes back byte for byte, a missing one raises ObjectNotFound, an unsafe key is
refused before any SQL runs, and a put is durable on its own — not part of
whatever transaction the caller has open.

It commits on its own connection, as it must in production, so each test
removes what it stored rather than relying on the per-test rollback.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.services.storage import (
    DatabaseStorage,
    LocalFilesystemStorage,
    ObjectNotFound,
    UnsafeStorageKey,
    build_artifact_key,
    build_storage_key,
    content_digest,
    get_storage,
)


@pytest.fixture
def storage(engine):
    backend = DatabaseStorage(engine=engine)
    yield backend
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM stored_objects WHERE key LIKE 'resumes/9%' OR key LIKE 'tailored/9%'"))


def _key(digit: str = "a") -> str:
    return build_storage_key(user_id=9001, content_sha256=digit * 64, extension="pdf")


def test_a_stored_object_comes_back_byte_for_byte(storage):
    data = bytes(range(256)) * 64
    storage.put(_key(), data, content_type="application/pdf")

    assert storage.exists(_key()) is True
    assert storage.get(_key()) == data


def test_a_missing_object_raises(storage):
    with pytest.raises(ObjectNotFound):
        storage.get(_key("b"))
    assert storage.exists(_key("b")) is False


def test_putting_the_same_key_again_replaces_it(storage):
    storage.put(_key(), b"first", content_type="application/pdf")
    storage.put(_key(), b"second", content_type="application/octet-stream")

    assert storage.get(_key()) == b"second"


def test_delete_removes_and_is_idempotent(storage):
    storage.put(_key(), b"x", content_type="application/pdf")
    storage.delete(_key())
    storage.delete(_key())

    assert storage.exists(_key()) is False


def test_the_row_records_size_and_type(storage, engine):
    key = build_artifact_key(user_id=9002, content_sha256=content_digest(b"docx"), extension="docx")
    storage.put(key, b"docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT content_type, size_bytes FROM stored_objects WHERE key = :key"), {"key": key}
        ).one()
    assert row.size_bytes == 4
    assert row.content_type.endswith("wordprocessingml.document")


def test_a_put_is_durable_whatever_the_callers_transaction_does(storage, db):
    # The caller's session has work in flight and then rolls back — the bytes
    # stay, exactly as a file written under the filesystem backend would.
    db.execute(text("SELECT 1"))
    storage.put(_key(), b"kept", content_type="application/pdf")
    db.rollback()

    assert storage.get(_key()) == b"kept"


@pytest.mark.parametrize(
    "key",
    ["../etc/passwd", "resumes/1/../../x.pdf", "/abs/path.pdf", "RESUMES/1/a.pdf", "resumes/1/a", ""],
)
def test_an_unsafe_key_is_refused_on_every_operation(storage, key):
    with pytest.raises(UnsafeStorageKey):
        storage.put(key, b"x", content_type="application/pdf")
    with pytest.raises(UnsafeStorageKey):
        storage.get(key)
    with pytest.raises(UnsafeStorageKey):
        storage.exists(key)
    with pytest.raises(UnsafeStorageKey):
        storage.delete(key)


def test_configuration_selects_the_backend(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "database")
    assert isinstance(get_storage(), DatabaseStorage)

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "filesystem")
    assert isinstance(get_storage(), LocalFilesystemStorage)


def test_an_unknown_backend_name_is_refused_at_startup():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="postgresql+psycopg://x@localhost/x", STORAGE_BACKEND="s3-maybe")
    assert Settings(DATABASE_URL="postgresql+psycopg://x@localhost/x", STORAGE_BACKEND=" Database ").STORAGE_BACKEND == "database"
