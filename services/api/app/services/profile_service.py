"""The Profile application layer.

Plain module-level functions taking (db, *, user_id, ...). No class, because
there is no state to hold; no repository, because a Session already is one.

Two rules run through everything here:

1. Every mutating call takes the profiles row lock FIRST, before reading
   anything it will act on. Lock-then-read is the load-bearing order; reading
   first and locking second reintroduces the race it was meant to close.

2. The database owns truth. This layer pre-checks only facts it must already
   have loaded to do its job (a version's status, which it holds anyway), and
   never issues a query whose sole purpose is to re-verify a constraint.
   Uniqueness, pairing and immutability are left to the schema and translated
   to HTTP by app/api/errors.py.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Certification,
    Education,
    Experience,
    Profile,
    ProfileLink,
    ProfileVersion,
    Project,
    ProjectTechnology,
    Skill,
)
from app.models.profile_link import OWNER_PERSONAL, OWNER_PROJECT
from app.models.profile_version import (
    CREATED_BY_AI,
    CREATED_BY_USER,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
)
from app.schemas.snapshot import Snapshot
from app.services.errors import ConflictError, NotFoundError


@contextmanager
def _transaction(db: Session) -> Iterator[None]:
    """The unit of work.

    Commit happens here, inside the handler's call stack, so a failure can
    still become a response. A yield-dependency teardown cannot do this:
    FastAPI runs it after the response body is already written.
    """
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def _lock_profile(db: Session, user_id: int) -> Profile:
    """Take the write lock for this user's profile, or 404.

    FOR UPDATE on the profiles row serialises every write for one profile,
    which is what makes version-number allocation safe without a retry loop.
    """
    profile = db.scalar(
        select(Profile).where(Profile.user_id == user_id).with_for_update()
    )
    if profile is None:
        raise NotFoundError("No profile exists for this user.")
    return profile


def _load_version(db: Session, profile_id: int, version_number: int) -> ProfileVersion:
    """Load a version scoped to the profile, so a wrong number is a 404 inside
    your own profile rather than a peek at someone else's row."""
    version = db.scalar(
        select(ProfileVersion)
        .where(
            ProfileVersion.profile_id == profile_id,
            ProfileVersion.version_number == version_number,
        )
        .options(
            selectinload(ProfileVersion.links),
            selectinload(ProfileVersion.experiences),
            selectinload(ProfileVersion.education),
            selectinload(ProfileVersion.skills),
            selectinload(ProfileVersion.projects).selectinload(Project.technologies),
            selectinload(ProfileVersion.certifications),
        )
    )
    if version is None:
        raise NotFoundError(f"Version {version_number} does not exist.")
    return version


# --- profile -----------------------------------------------------------------


def create_profile(db: Session, *, user_id: int) -> Profile:
    """Create the caller's profile.

    No pre-check that one already exists: uq_profiles_user_id owns that, and
    INSERT-then-translate is race-free where a check-then-insert is not.
    """
    with _transaction(db):
        profile = Profile(user_id=user_id)
        db.add(profile)
        db.flush()
    return profile


def get_profile(db: Session, *, user_id: int) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.user_id == user_id))
    if profile is None:
        raise NotFoundError("No profile exists for this user.")
    return profile


# --- versions ----------------------------------------------------------------


def _allocate_draft(
    db: Session,
    *,
    profile: Profile,
    created_by_type: str,
    created_by_user_id: int | None,
) -> ProfileVersion:
    """Open the next draft for an already-locked profile.

    Caller must hold the profiles row lock. Under it, max()+1 is safe: a
    concurrent caller blocks and reads the committed maximum rather than a
    stale one. The UNIQUE(profile_id, version_number) constraint stays the
    safety net, not the strategy.
    """
    highest = db.scalar(
        select(func.max(ProfileVersion.version_number)).where(
            ProfileVersion.profile_id == profile.id
        )
    )
    version = ProfileVersion(
        profile_id=profile.id,
        version_number=(highest or 0) + 1,
        status=STATUS_DRAFT,
        created_by_type=created_by_type,
        created_by_user_id=created_by_user_id,
    )
    db.add(version)
    db.flush()
    return version


def create_draft_version(
    db: Session,
    *,
    user_id: int,
    created_by_type: str = CREATED_BY_USER,
    created_by_user_id: int | None = None,
) -> ProfileVersion:
    """Allocate the next version number and open an empty draft."""
    with _transaction(db):
        profile = _lock_profile(db, user_id)
        version = _allocate_draft(
            db,
            profile=profile,
            created_by_type=created_by_type,
            created_by_user_id=(
                user_id if created_by_type == CREATED_BY_USER else created_by_user_id
            ),
        )

    return _load_version(db, version.profile_id, version.version_number)


def create_draft_version_with_snapshot(
    db: Session,
    *,
    user_id: int,
    snapshot: Snapshot,
    created_by_type: str = CREATED_BY_AI,
    created_by_user_id: int | None = None,
) -> ProfileVersion:
    """Open a draft and fill it, in ONE transaction.

    Resume ingestion needs this. Calling create_draft_version() and then
    replace_snapshot() would be two committed transactions, and a failure
    between them would leave an empty draft in the user's history with a
    version number permanently burnt.
    """
    with _transaction(db):
        profile = _lock_profile(db, user_id)
        version = _allocate_draft(
            db,
            profile=profile,
            created_by_type=created_by_type,
            created_by_user_id=created_by_user_id,
        )
        _apply_snapshot(version, snapshot)
        db.flush()

    db.expire_all()
    return _load_version(db, profile.id, version.version_number)


def list_versions(db: Session, *, user_id: int) -> list[ProfileVersion]:
    profile = get_profile(db, user_id=user_id)
    return list(
        db.scalars(
            select(ProfileVersion)
            .where(ProfileVersion.profile_id == profile.id)
            .order_by(ProfileVersion.version_number)
        )
    )


def get_version(db: Session, *, user_id: int, version_number: int) -> ProfileVersion:
    profile = get_profile(db, user_id=user_id)
    return _load_version(db, profile.id, version_number)


def get_current_version(db: Session, *, user_id: int) -> ProfileVersion:
    profile = get_profile(db, user_id=user_id)
    version = db.scalar(
        select(ProfileVersion).where(
            ProfileVersion.profile_id == profile.id,
            ProfileVersion.is_current.is_(True),
        )
    )
    if version is None:
        raise NotFoundError("This profile has no current version.")
    return _load_version(db, profile.id, version.version_number)


# --- snapshot ----------------------------------------------------------------


def replace_snapshot(
    db: Session, *, user_id: int, version_number: int, snapshot: Snapshot
) -> ProfileVersion:
    """Replace a draft version's snapshot wholesale.

    Full replace rather than per-entity CRUD because a version IS a document:
    it is built, frozen, and referenced as a whole. It is also idempotent and
    cannot leave a half-edited snapshot behind.
    """
    with _transaction(db):
        profile = _lock_profile(db, user_id)
        version = _load_version(db, profile.id, version_number)

        # Pre-checked because the row is already loaded, so it costs no query
        # and turns an opaque trigger raise into a message with the number in
        # it. The trigger remains the authority and the backstop.
        if version.status == STATUS_CONFIRMED:
            raise ConflictError(
                f"Version {version_number} is confirmed and cannot be modified."
            )

        # Personal links only, not `version.links.clear()`: that collection
        # also holds every project's own links (see
        # `ProfileVersion.personal_links`), and those are cleared below by
        # `version.projects.clear()`, through `Project.links`'s own cascade.
        # Removing them here TOO — a second delete-orphan cascade converging
        # on the same row from a second relationship — is exactly the
        # ambiguity SQLAlchemy's cascade docs warn against, and there is no
        # need to risk it when one path already owns the deletion.
        for link in list(version.personal_links):
            version.links.remove(link)
        version.experiences.clear()
        version.education.clear()
        version.skills.clear()
        version.projects.clear()
        version.certifications.clear()
        db.flush()

        _apply_snapshot(version, snapshot)
        db.flush()

    # The collections in the identity map are in insertion order; expiring
    # makes the reload apply each relationship's order_by, so what PUT returns
    # is byte-for-byte what GET returns.
    db.expire_all()
    return _load_version(db, profile.id, version_number)


def _apply_snapshot(version: ProfileVersion, snapshot: Snapshot) -> None:
    """Translate the submitted document into rows.

    Fields left unset are omitted rather than written as NULL, so the column's
    server_default applies — that is how an experience with no stated precision
    picks up 'day' from the schema instead of failing the pairing CHECK.
    """

    def fields(item, exclude=()):
        return item.model_dump(exclude_unset=False, exclude=set(exclude))

    # Assigned unconditionally, including when the snapshot carries no contact
    # details: this function replaces a version's content wholesale, so leaving
    # the old values in place on an edit that cleared them would make a removal
    # silently fail.
    contact = snapshot.contact
    version.full_name = contact.full_name if contact else None
    version.email = contact.email if contact else None
    version.phone = contact.phone if contact else None
    version.location = contact.location if contact else None
    version.summary = snapshot.summary

    for item in snapshot.links:
        version.links.append(ProfileLink(owner_type=OWNER_PERSONAL, **fields(item)))
    for item in snapshot.experiences:
        version.experiences.append(Experience(**_drop_none_precision(fields(item))))
    for item in snapshot.education:
        version.education.append(Education(**_drop_none_precision(fields(item))))
    for item in snapshot.skills:
        version.skills.append(Skill(**fields(item)))
    for item in snapshot.certifications:
        version.certifications.append(Certification(**fields(item)))
    for item in snapshot.projects:
        project = Project(
            **_drop_none_precision(fields(item, exclude=("technologies", "links")))
        )
        for name in item.technologies:
            project.technologies.append(ProjectTechnology(name=name))
        for link in item.links:
            # `profile_version_id` is set explicitly rather than left to the
            # relationship: a project-owned link is reached through
            # `Project.links` here, but `ProfileVersion.links` — the single,
            # id-ordered list every reader of "every link this version holds"
            # relies on, `personal_links` included — is keyed off this column
            # directly, not off a join through `projects`.
            project.links.append(
                ProfileLink(
                    owner_type=OWNER_PROJECT,
                    profile_version_id=version.id,
                    **fields(link),
                )
            )
        version.projects.append(project)


def _drop_none_precision(values: dict) -> dict:
    """Let the column default speak when no precision was stated."""
    return {
        key: value
        for key, value in values.items()
        if not (key.endswith("_precision") and value is None)
    }


# --- confirmation ------------------------------------------------------------


def confirm_version(
    db: Session, *, user_id: int, version_number: int, make_current: bool = True
) -> ProfileVersion:
    """Freeze a draft, optionally promoting it to current."""
    with _transaction(db):
        profile = _lock_profile(db, user_id)
        version = _load_version(db, profile.id, version_number)

        if version.status == STATUS_CONFIRMED:
            raise ConflictError(f"Version {version_number} is already confirmed.")

        if make_current:
            # A Core UPDATE, not an attribute assignment on the sibling row.
            # SQLAlchemy orders UPDATEs within a flush by primary key for
            # deadlock avoidance, not by the order attributes were set, so
            # dirtying both rows could emit the promote before the demote —
            # and uq_profile_versions_current is a plain partial index,
            # checked per row written, with no deferral to fall back on.
            db.execute(
                update(ProfileVersion)
                .where(
                    ProfileVersion.profile_id == profile.id,
                    ProfileVersion.is_current.is_(True),
                )
                .values(is_current=False)
                .execution_options(synchronize_session=False)
            )

        version.status = STATUS_CONFIRMED
        version.confirmed_at = datetime.now(timezone.utc)
        version.is_current = make_current
        db.flush()

    # synchronize_session=False leaves stale is_current on any sibling already
    # in the identity map, so expire before reading anything back.
    db.expire_all()
    return _load_version(db, profile.id, version_number)


def make_version_current(db: Session, *, user_id: int, version_number: int) -> ProfileVersion:
    """Promote an already-confirmed version to current.

    Confirming with "make current" unticked used to be a one-way door: the
    version was frozen, nothing was current, and no route could ever make it so
    — the dashboard and the profile page then said nothing was current for good.
    `is_current` is the one column the immutability trigger lets a confirmed
    version change, so this changes that and nothing else, under the same
    profile lock and in the same demote-then-promote order `confirm_version`
    uses (see the comment there on why the demote is a Core UPDATE). Asking for
    the version that is already current is a no-op, not an error.
    """
    with _transaction(db):
        profile = _lock_profile(db, user_id)
        version = _load_version(db, profile.id, version_number)

        if version.status != STATUS_CONFIRMED:
            raise ConflictError(
                f"Version {version_number} is a draft. Confirm it before making it current."
            )

        if not version.is_current:
            db.execute(
                update(ProfileVersion)
                .where(
                    ProfileVersion.profile_id == profile.id,
                    ProfileVersion.is_current.is_(True),
                )
                .values(is_current=False)
                .execution_options(synchronize_session=False)
            )
            version.is_current = True
            db.flush()

    db.expire_all()
    return _load_version(db, profile.id, version_number)
