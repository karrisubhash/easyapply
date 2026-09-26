"""Constraints that make a ProfileVersion trustworthy."""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Profile, ProfileVersion, User


def test_version_numbers_are_unique_per_profile(db, profile, make_version):
    make_version(version_number=1)
    make_version(version_number=1, flush=False)
    with pytest.raises(IntegrityError, match="uq_profile_versions_profile_id_version_number"):
        db.flush()
    db.rollback()


def test_the_same_version_number_is_fine_on_another_profile(db, make_version):
    other_user = User(email="second@example.com")
    db.add(other_user)
    db.flush()
    other_profile = Profile(user_id=other_user.id)
    db.add(other_profile)
    db.flush()

    make_version(version_number=1)
    make_version(version_number=1, target=other_profile)
    db.flush()  # no error


def test_version_numbers_start_at_one(db, make_version):
    make_version(version_number=0, flush=False)
    with pytest.raises(IntegrityError, match="ck_profile_versions_version_number_positive"):
        db.flush()
    db.rollback()


def test_status_is_restricted_to_known_values(db, make_version):
    make_version(status="whatever", flush=False)
    with pytest.raises(IntegrityError, match="ck_profile_versions_status"):
        db.flush()
    db.rollback()


def test_a_draft_cannot_carry_a_confirmation_timestamp(db, profile, user):
    db.add(
        ProfileVersion(
            profile_id=profile.id,
            version_number=1,
            status="draft",
            created_by_type="user",
            created_by_user_id=user.id,
            confirmed_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError, match="ck_profile_versions_confirmed_at_matches_status"):
        db.flush()
    db.rollback()


def test_a_confirmed_version_must_record_when(db, profile, user):
    db.add(
        ProfileVersion(
            profile_id=profile.id,
            version_number=1,
            status="confirmed",
            created_by_type="user",
            created_by_user_id=user.id,
            confirmed_at=None,
        )
    )
    with pytest.raises(IntegrityError, match="ck_profile_versions_confirmed_at_matches_status"):
        db.flush()
    db.rollback()


def test_a_draft_can_never_be_the_current_version(db, make_version):
    make_version(status="draft", is_current=True, flush=False)
    with pytest.raises(IntegrityError, match="ck_profile_versions_current_is_confirmed"):
        db.flush()
    db.rollback()


def test_only_one_current_version_per_profile(db, make_version, confirm):
    first = make_version(version_number=1)
    confirm(first, make_current=True)

    second = make_version(version_number=2)
    confirm(second)
    second.is_current = True
    with pytest.raises(IntegrityError, match="uq_profile_versions_current"):
        db.flush()
    db.rollback()


def test_promoting_a_version_requires_demoting_the_old_one_first(db, make_version, confirm):
    """The partial unique index is an index, not a deferrable constraint, so
    the two updates cannot be reordered or batched."""
    first = make_version(version_number=1)
    confirm(first, make_current=True)
    second = make_version(version_number=2)
    confirm(second)

    first.is_current = False
    db.flush()
    second.is_current = True
    db.flush()

    current = db.query(ProfileVersion).filter_by(is_current=True).all()
    assert [v.version_number for v in current] == [2]


def test_two_profiles_may_each_have_a_current_version(db, make_version, confirm):
    other_user = User(email="third@example.com")
    db.add(other_user)
    db.flush()
    other_profile = Profile(user_id=other_user.id)
    db.add(other_profile)
    db.flush()

    confirm(make_version(version_number=1), make_current=True)
    confirm(make_version(version_number=1, target=other_profile), make_current=True)
    db.flush()  # no error


def test_created_by_type_is_restricted_to_known_values(db, make_version):
    make_version(created_by_type="wizard", created_by_user_id=None, flush=False)
    with pytest.raises(IntegrityError, match="ck_profile_versions_created_by_type"):
        db.flush()
    db.rollback()


def test_a_user_authored_version_must_name_the_user(db, make_version):
    make_version(created_by_type="user", created_by_user_id=None, flush=False)
    with pytest.raises(IntegrityError, match="ck_profile_versions_created_by_user_present"):
        db.flush()
    db.rollback()


def test_extraction_may_author_a_draft_without_a_user(db, make_version):
    version = make_version(created_by_type="ai", created_by_user_id=None)
    assert version.id is not None


def test_timestamps_are_timezone_aware(db, profile, make_version):
    version = make_version()
    db.refresh(version)
    assert version.created_at.tzinfo is not None
    assert profile.created_at.tzinfo is not None
