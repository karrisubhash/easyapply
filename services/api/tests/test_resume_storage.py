"""The storage abstraction, and the path-traversal boundary it defends."""

import pytest

from app.services.storage import (
    InMemoryStorage,
    LocalFilesystemStorage,
    ObjectNotFound,
    UnsafeStorageKey,
    build_storage_key,
    content_digest,
)


@pytest.fixture
def local_storage(tmp_path):
    return LocalFilesystemStorage(root=tmp_path)


def test_a_stored_object_comes_back(local_storage):
    key = build_storage_key(user_id=1, content_sha256="a" * 64, extension="pdf")
    local_storage.put(key, b"%PDF-content", content_type="application/pdf")

    assert local_storage.exists(key) is True
    assert local_storage.get(key) == b"%PDF-content"


def test_a_missing_object_raises(local_storage):
    with pytest.raises(ObjectNotFound):
        local_storage.get("resumes/1/" + "b" * 64 + ".pdf")


def test_delete_is_idempotent(local_storage):
    key = build_storage_key(user_id=1, content_sha256="c" * 64, extension="pdf")
    local_storage.put(key, b"x", content_type="application/pdf")
    local_storage.delete(key)
    local_storage.delete(key)
    assert local_storage.exists(key) is False


def test_the_key_is_derived_from_content_not_from_the_filename():
    """The uploaded filename is attacker-controlled and never reaches a path."""
    digest = content_digest(b"some bytes")
    key = build_storage_key(user_id=7, content_sha256=digest, extension="PDF")

    assert key == f"resumes/7/{digest}.pdf"
    assert "resume" in key and ".." not in key


def test_the_same_content_yields_the_same_key():
    digest = content_digest(b"identical")
    first = build_storage_key(user_id=3, content_sha256=digest, extension="pdf")
    second = build_storage_key(user_id=3, content_sha256=digest, extension="pdf")
    assert first == second


def test_different_users_never_share_a_key():
    digest = content_digest(b"identical")
    assert build_storage_key(user_id=1, content_sha256=digest, extension="pdf") != (
        build_storage_key(user_id=2, content_sha256=digest, extension="pdf")
    )


@pytest.mark.parametrize(
    "key",
    [
        "../escape.pdf",
        "resumes/../../etc/passwd.pdf",
        "/absolute/path.pdf",
        "resumes/1/no-extension",
        "resumes/1/x.exe.pdf/../../y.pdf",
    ],
)
def test_a_traversing_key_is_refused(local_storage, key):
    with pytest.raises(UnsafeStorageKey):
        local_storage.put(key, b"x", content_type="application/pdf")


def test_a_traversing_key_is_refused_on_read_too(local_storage):
    """A key read back from the database is still checked; a database is not a
    trust boundary."""
    with pytest.raises(UnsafeStorageKey):
        local_storage.get("../../../etc/passwd.pdf")


def test_a_dangerous_extension_is_refused():
    with pytest.raises(UnsafeStorageKey):
        build_storage_key(user_id=1, content_sha256="d" * 64, extension="../sh")


def test_writes_do_not_leave_partial_objects(local_storage, tmp_path):
    key = build_storage_key(user_id=1, content_sha256="e" * 64, extension="pdf")
    local_storage.put(key, b"payload", content_type="application/pdf")

    leftovers = list(tmp_path.rglob("*.partial"))
    assert leftovers == []


def test_the_in_memory_backend_satisfies_the_same_contract():
    storage = InMemoryStorage()
    key = build_storage_key(user_id=1, content_sha256="f" * 64, extension="docx")

    storage.put(key, b"bytes", content_type="application/octet-stream")
    assert storage.get(key) == b"bytes"
    assert storage.exists(key) is True

    storage.delete(key)
    with pytest.raises(ObjectNotFound):
        storage.get(key)
