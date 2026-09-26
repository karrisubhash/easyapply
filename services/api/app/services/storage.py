"""Object storage, behind an interface.

The processing code never learns where bytes live. It holds a storage key — an
opaque string it received from `put` — and passes it back to `get`. That is the
whole contract, and it is why a new backend (DatabaseStorage below; an Azure
Blob one later) is a new class in this file rather than a change to any
resume-processing logic.

Deliberately NOT part of the interface: anything that leaks a filesystem. There
is no `path()` and no `full_path`, because the moment one exists a caller builds
a string with it and the abstraction is over.
"""

import hashlib
import re
from pathlib import Path
from typing import Protocol

from app.core.config import settings

# A key is lowercase hex, digits, slashes, dashes and a single extension. This
# is the anti-path-traversal rule, and it is enforced on the way in AND on the
# way out: a key read back from the database is still checked before it touches
# a filesystem, because a database is not a trust boundary.
_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9/_-]*\.[a-z0-9]{2,5}$")


class StorageError(RuntimeError):
    """The backend could not satisfy the request."""


class ObjectNotFound(StorageError):
    """No object exists under that key."""


class UnsafeStorageKey(StorageError):
    """The key is not one this layer is willing to touch."""


def build_storage_key(*, user_id: int, content_sha256: str, extension: str) -> str:
    """Derive a storage key from content, never from the uploaded filename.

    The original filename is attacker-controlled and is kept only as metadata
    for display. Naming the object after its own hash means the key is safe by
    construction, is stable across retries of the same upload, and cannot
    collide between users because the user id scopes it.
    """
    safe_extension = extension.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{2,5}", safe_extension):
        raise UnsafeStorageKey(f"refusing extension {extension!r}")
    return f"resumes/{user_id}/{content_sha256}.{safe_extension}"


def build_artifact_key(*, user_id: int, content_sha256: str, extension: str) -> str:
    """The same rule as `build_storage_key`, under a different prefix.

    Separate prefixes because the two object classes have genuinely different
    lifecycles: an uploaded resume is the user's own file and is kept as long as
    the account is, while a tailored artifact is derived data that can be
    regenerated from the run that produced it. A retention rule that expires one
    and not the other is a prefix filter, which is only possible if the prefixes
    differ from the start.

    Named after the content hash for the same reasons the resume key is: safe by
    construction, stable across a re-render of the same document, and impossible
    to collide across users.
    """
    safe_extension = extension.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{2,5}", safe_extension):
        raise UnsafeStorageKey(f"refusing extension {extension!r}")
    return f"tailored/{user_id}/{content_sha256}.{safe_extension}"


def content_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageBackend(Protocol):
    """What resume processing is allowed to know about storage."""

    def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


class LocalFilesystemStorage:
    """Development backend: one file per object, under a single root.

    Stands in for object storage well enough to prove the interface, and badly
    enough that nobody will mistake it for production: no redundancy, no
    server-side encryption, no lifecycle rules.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root or settings.RESUME_STORAGE_ROOT).resolve()

    def _resolve(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise UnsafeStorageKey(f"refusing storage key {key!r}")

        candidate = (self._root / key).resolve()
        # Belt and braces. The regex already forbids "..", but the containment
        # check is what actually guarantees we stay inside the root — on
        # Windows in particular, where symlinks and short names can surprise you.
        if not candidate.is_relative_to(self._root):
            raise UnsafeStorageKey(f"storage key escapes the root: {key!r}")
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write to a sibling temp file and rename, so a crash mid-write cannot
        # leave a half-written object under a key the database already believes in.
        staging = target.with_suffix(target.suffix + ".partial")
        staging.write_bytes(data)
        staging.replace(target)

    def get(self, key: str) -> bytes:
        target = self._resolve(key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)


class InMemoryStorage:
    """For tests that care about the processing pipeline, not about disks."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        if not _SAFE_KEY.match(key):
            raise UnsafeStorageKey(f"refusing storage key {key!r}")
        self._objects[key] = data

    def get(self, key: str) -> bytes:
        try:
            return self._objects[key]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        return key in self._objects

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)


class DatabaseStorage:
    """Objects as rows of `stored_objects`, in the application's own database.

    For a deployment with no persistent disk and no object store — the free-tier
    staging environment, whose container filesystem is wiped on every restart
    (docs/staging-deployment.md). Not a production answer: bytes in the database
    grow its backups and its storage bill, and a real object store is the
    intended backend. It is here because "the documents vanished overnight" is
    worse than both.

    Each operation runs in its own short transaction on its own connection, never
    the caller's session — the same thing the filesystem backend gives: once
    `put` returns, the bytes are durable, whatever later happens to the
    transaction that records their key. A put of an existing key replaces it,
    which for content-addressed keys means writing the same bytes again.

    The key rule is the filesystem backend's, checked in both directions, so a
    deployment can move between the two without a key that one of them refuses.
    """

    def __init__(self, engine=None) -> None:
        if engine is None:
            from app.db.session import engine as application_engine

            engine = application_engine
        self._engine = engine

    @staticmethod
    def _checked(key: str) -> str:
        if not _SAFE_KEY.match(key):
            raise UnsafeStorageKey(f"refusing storage key {key!r}")
        return key

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from app.models.stored_object import StoredObject

        values = {
            "key": self._checked(key),
            "content_type": content_type,
            "data": data,
            "size_bytes": len(data),
        }
        statement = insert(StoredObject.__table__).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["key"],
            set_={name: statement.excluded[name] for name in ("content_type", "data", "size_bytes")},
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, key: str) -> bytes:
        from sqlalchemy import select

        from app.models.stored_object import StoredObject

        table = StoredObject.__table__
        with self._engine.connect() as connection:
            data = connection.execute(
                select(table.c.data).where(table.c.key == self._checked(key))
            ).scalar_one_or_none()
        if data is None:
            raise ObjectNotFound(key)
        return bytes(data)

    def exists(self, key: str) -> bool:
        from sqlalchemy import select

        from app.models.stored_object import StoredObject

        table = StoredObject.__table__
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    select(table.c.key).where(table.c.key == self._checked(key))
                ).first()
                is not None
            )

    def delete(self, key: str) -> None:
        from app.models.stored_object import StoredObject

        table = StoredObject.__table__
        with self._engine.begin() as connection:
            connection.execute(table.delete().where(table.c.key == self._checked(key)))


def get_storage() -> StorageBackend:
    """The application's storage backend, as STORAGE_BACKEND names it.

    A function rather than a module-level singleton so a FastAPI dependency can
    override it, and so a backend is selected by configuration without any
    import-time work. `filesystem` (the default) is LocalFilesystemStorage;
    `database` is DatabaseStorage.
    """
    if settings.STORAGE_BACKEND == "database":
        return DatabaseStorage()
    return LocalFilesystemStorage()
