"""The point of the whole design: what did EasyApply know, and when."""

from datetime import date, datetime, timezone

from app.models import ProfileVersion
from tests.factories import a_skill, an_experience


def _snapshot_version(db, profile, user, version_number, skills, copy_from=None):
    """Build a version the way the application must: draft, fill, confirm.

    The triggers refuse snapshot rows written to an already-confirmed version,
    so this order is not a style preference.
    """
    version = ProfileVersion(
        profile_id=profile.id,
        version_number=version_number,
        status="draft",
        created_by_type="user",
        created_by_user_id=user.id,
    )
    db.add(version)
    db.flush()

    if copy_from is not None:
        for experience in copy_from.experiences:
            an_experience(
                db,
                version,
                company=experience.company,
                job_title=experience.job_title,
                start_date=experience.start_date,
            )

    for name in skills:
        a_skill(db, version, name=name)

    version.status = "confirmed"
    version.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    return version


def test_a_later_version_does_not_rewrite_an_earlier_one(db, profile, user):
    """The example from the architecture: V1 gains FastAPI and becomes V2."""
    v1 = _snapshot_version(db, profile, user, 1, ["Python", "Django", "PostgreSQL"])
    v1.is_current = True
    db.flush()

    v2 = _snapshot_version(
        db, profile, user, 2, ["Python", "Django", "PostgreSQL", "FastAPI"]
    )
    v1.is_current = False
    db.flush()
    v2.is_current = True
    db.flush()

    db.expire(v1)
    db.expire(v2)

    assert sorted(s.name for s in v1.skills) == ["Django", "PostgreSQL", "Python"]
    assert sorted(s.name for s in v2.skills) == [
        "Django",
        "FastAPI",
        "PostgreSQL",
        "Python",
    ]
    assert v1.is_current is False
    assert v2.is_current is True


def test_an_old_version_stays_reconstructible_after_the_profile_moves_on(
    db, profile, user
):
    """A resume generated from V1 can still be explained once V2 is current."""
    v1 = _snapshot_version(db, profile, user, 1, ["Python"])
    generated_from = v1.id

    _snapshot_version(db, profile, user, 2, ["Python", "Rust"], copy_from=v1)

    reconstructed = db.get(ProfileVersion, generated_from)
    db.expire(reconstructed)

    assert reconstructed.version_number == 1
    assert [s.name for s in reconstructed.skills] == ["Python"]
    assert reconstructed.confirmed_at is not None


def test_the_snapshot_carries_its_own_copy_of_shared_data(db, profile, user):
    """Duplication is the deliberate cost of reproducibility."""
    draft = ProfileVersion(
        profile_id=profile.id,
        version_number=1,
        status="draft",
        created_by_type="user",
        created_by_user_id=user.id,
    )
    db.add(draft)
    db.flush()
    an_experience(db, draft, company="APTRANSCO")
    a_skill(db, draft, name="Python")
    draft.status = "confirmed"
    draft.confirmed_at = datetime.now(timezone.utc)
    db.flush()

    v2 = _snapshot_version(db, profile, user, 2, ["Python"], copy_from=draft)

    db.expire(draft)
    db.expire(v2)

    assert {s.id for s in draft.skills}.isdisjoint({s.id for s in v2.skills})
    assert [s.name for s in draft.skills] == [s.name for s in v2.skills]
    assert [e.company for e in draft.experiences] == [e.company for e in v2.experiences]
    assert {e.id for e in draft.experiences}.isdisjoint({e.id for e in v2.experiences})


def test_every_confirmed_version_records_who_confirmed_it_and_when(db, profile, user):
    version = _snapshot_version(db, profile, user, 1, ["Python"])
    db.refresh(version)

    assert version.created_by_type == "user"
    assert version.created_by_user_id == user.id
    assert version.confirmed_at is not None
    assert version.confirmed_at.tzinfo is not None


def test_experiences_come_back_newest_first(db, profile, user):
    version = ProfileVersion(
        profile_id=profile.id,
        version_number=1,
        status="draft",
        created_by_type="ai",
    )
    db.add(version)
    db.flush()

    an_experience(db, version, company="Older", start_date=date(2024, 1, 1))
    an_experience(db, version, company="Newer", start_date=date(2026, 5, 15))
    db.expire(version)

    assert [e.company for e in version.experiences] == ["Newer", "Older"]
