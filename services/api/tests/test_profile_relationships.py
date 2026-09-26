"""Profile <-> User and Profile <-> ProfileVersion relationships."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Profile, ProfileVersion, User


def test_profile_belongs_to_a_user(db, user, profile):
    assert profile.user_id == user.id
    assert profile.user.email == user.email


def test_user_can_have_only_one_profile(db, user, profile):
    db.add(Profile(user_id=user.id))
    with pytest.raises(IntegrityError, match="uq_profiles_user_id"):
        db.flush()
    db.rollback()


def test_profile_requires_an_existing_user(db):
    db.add(Profile(user_id=987654))
    with pytest.raises(IntegrityError, match="fk_profiles_user_id"):
        db.flush()
    db.rollback()


def test_deleting_a_user_with_a_profile_is_restricted(db, user, profile):
    """Erasing a user must be a deliberate procedure, not a side effect."""
    with pytest.raises(IntegrityError, match="fk_profiles_user_id"):
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    db.rollback()


def test_deleting_a_profile_cascades_to_its_draft_versions(db, profile, make_version):
    make_version(version_number=1)
    make_version(version_number=2)
    db.delete(profile)
    db.flush()

    remaining = db.query(ProfileVersion).filter_by(profile_id=profile.id).count()
    assert remaining == 0


def test_versions_are_ordered_by_version_number(db, profile, make_version):
    make_version(version_number=3)
    make_version(version_number=1)
    make_version(version_number=2)
    db.expire(profile)

    assert [v.version_number for v in profile.versions] == [1, 2, 3]


def test_version_back_populates_its_profile(db, profile, make_version):
    version = make_version()
    assert version.profile.id == profile.id


def test_the_author_of_a_version_cannot_be_deleted(db, profile, make_version):
    """The audit trail outlives convenience: a referenced author cannot vanish.

    The author is a separate account from the profile owner, so the only thing
    standing in the way of the DELETE is created_by_user_id.
    """
    author = User(email="reviewer@example.com")
    db.add(author)
    db.flush()
    make_version(created_by_user_id=author.id)

    with pytest.raises(IntegrityError, match="fk_profile_versions_created_by_user_id"):
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": author.id})
    db.rollback()


def test_identity_does_not_depend_on_the_profile_domain(db):
    """User has no profile attribute: the dependency runs one way only."""
    assert not hasattr(User, "profile")
