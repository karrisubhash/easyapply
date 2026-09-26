"""Confirmed versions are immutable, and the database is what enforces it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Experience, Skill

from tests.factories import an_experience, a_skill


def test_a_draft_snapshot_can_be_edited(db, make_version):
    version = make_version()
    experience = an_experience(db, version)

    experience.job_title = "Senior AI Developer"
    db.flush()
    db.refresh(experience)
    assert experience.job_title == "Senior AI Developer"


def test_a_draft_snapshot_row_can_be_deleted(db, make_version):
    version = make_version()
    experience = an_experience(db, version)
    db.delete(experience)
    db.flush()
    assert db.query(Experience).filter_by(id=experience.id).count() == 0


def test_a_confirmed_snapshot_row_cannot_be_changed(db, make_version, confirm):
    version = make_version()
    experience = an_experience(db, version)
    confirm(version)

    experience.company = "Somewhere Else"
    with pytest.raises(IntegrityError, match="immutable"):
        db.flush()
    db.rollback()


def test_a_confirmed_snapshot_row_cannot_be_deleted(db, make_version, confirm):
    version = make_version()
    experience = an_experience(db, version)
    confirm(version)

    db.delete(experience)
    with pytest.raises(IntegrityError, match="immutable"):
        db.flush()
    db.rollback()


def test_rows_cannot_be_added_to_a_confirmed_version(db, make_version, confirm):
    """The INSERT path: a confirmed snapshot cannot grow after the fact."""
    version = make_version()
    confirm(version)

    an_experience(db, version, flush=False)
    with pytest.raises(IntegrityError, match="immutable"):
        db.flush()
    db.rollback()


def test_a_row_cannot_be_moved_into_a_confirmed_version(db, make_version, confirm):
    draft = make_version(version_number=1)
    experience = an_experience(db, draft)

    locked = make_version(version_number=2)
    confirm(locked)

    experience.profile_version_id = locked.id
    with pytest.raises(IntegrityError, match="immutable"):
        db.flush()
    db.rollback()


def test_a_confirmed_version_cannot_be_deleted(db, make_version, confirm):
    version = make_version()
    confirm(version)

    db.delete(version)
    with pytest.raises(IntegrityError, match="confirmed and cannot be deleted"):
        db.flush()
    db.rollback()


def test_a_confirmed_version_cannot_be_reopened(db, make_version, confirm):
    version = make_version()
    confirm(version)

    version.status = "draft"
    version.confirmed_at = None
    with pytest.raises(IntegrityError, match="only is_current may change"):
        db.flush()
    db.rollback()


def test_a_confirmed_version_cannot_be_renumbered(db, make_version, confirm):
    version = make_version(version_number=1)
    confirm(version)

    version.version_number = 99
    with pytest.raises(IntegrityError, match="only is_current may change"):
        db.flush()
    db.rollback()


def test_a_confirmed_version_may_still_change_is_current(db, make_version, confirm):
    """The one permitted mutation: promotion and demotion."""
    version = make_version()
    confirm(version, make_current=True)

    version.is_current = False
    db.flush()
    db.refresh(version)
    assert version.is_current is False


def test_a_draft_version_can_still_be_deleted(db, make_version):
    version = make_version()
    a_skill(db, version, name="Python")
    db.delete(version)
    db.flush()
    assert db.query(Skill).filter_by(profile_version_id=version.id).count() == 0


def test_project_technologies_are_protected_through_their_project(db, make_version, confirm):
    """The one child that reaches its version through another table."""
    version = make_version()
    db.execute(
        text(
            "INSERT INTO projects (profile_version_id, name) VALUES (:v, 'EasyApply')"
        ),
        {"v": version.id},
    )
    project_id = db.execute(
        text("SELECT id FROM projects WHERE profile_version_id = :v"), {"v": version.id}
    ).scalar()
    db.execute(
        text("INSERT INTO project_technologies (project_id, name) VALUES (:p, 'FastAPI')"),
        {"p": project_id},
    )
    confirm(version)

    with pytest.raises(IntegrityError, match="immutable"):
        db.execute(
            text("INSERT INTO project_technologies (project_id, name) VALUES (:p, 'Redis')"),
            {"p": project_id},
        )
    db.rollback()


# --- the columns and table added after the original guard ---------------------
#
# `a3f19c7d4e52` put the contact block and summary on profile_versions and added
# profile_links; neither was under the guard until `c3d9e1f47a20`. These pin the
# backstop directly, beneath the service's own pre-check.


@pytest.mark.parametrize(
    "column, value",
    [
        ("full_name", "Someone Else"),
        ("email", "someone@else.example"),
        ("phone", "+1 555 0100"),
        ("location", "Elsewhere"),
        ("summary", "A different person entirely."),
    ],
)
def test_a_confirmed_versions_contact_and_summary_cannot_change(
    db, make_version, confirm, column, value
):
    version = make_version()
    confirm(version)

    with pytest.raises(IntegrityError, match="only is_current may change"):
        db.execute(
            text(f"UPDATE profile_versions SET {column} = :value WHERE id = :v"),
            {"value": value, "v": version.id},
        )
    db.rollback()


def test_a_drafts_contact_and_summary_can_still_change(db, make_version):
    version = make_version()
    db.execute(
        text("UPDATE profile_versions SET email = 'new@example.com', summary = 'x' WHERE id = :v"),
        {"v": version.id},
    )
    assert (
        db.execute(text("SELECT email FROM profile_versions WHERE id = :v"), {"v": version.id}).scalar()
        == "new@example.com"
    )


def _a_link(db, version, url="https://example.com/me"):
    db.execute(
        text(
            "INSERT INTO profile_links (profile_version_id, url, label, owner_type) "
            "VALUES (:v, :url, 'Site', 'personal')"
        ),
        {"v": version.id, "url": url},
    )
    return db.execute(
        text("SELECT id FROM profile_links WHERE profile_version_id = :v AND url = :url"),
        {"v": version.id, "url": url},
    ).scalar()


def test_a_confirmed_versions_links_cannot_be_added(db, make_version, confirm):
    version = make_version()
    confirm(version)

    with pytest.raises(IntegrityError, match="immutable"):
        _a_link(db, version)
    db.rollback()


def test_a_confirmed_versions_links_cannot_be_changed_or_removed(db, make_version, confirm):
    version = make_version()
    link_id = _a_link(db, version)
    confirm(version)

    with pytest.raises(IntegrityError, match="immutable"):
        db.execute(
            text("UPDATE profile_links SET url = 'https://attacker.example' WHERE id = :id"),
            {"id": link_id},
        )
    db.rollback()


def test_a_confirmed_versions_links_cannot_be_deleted(db, make_version, confirm):
    version = make_version()
    link_id = _a_link(db, version)
    confirm(version)

    with pytest.raises(IntegrityError, match="immutable"):
        db.execute(text("DELETE FROM profile_links WHERE id = :id"), {"id": link_id})
    db.rollback()


def test_a_drafts_links_can_still_be_edited(db, make_version):
    version = make_version()
    link_id = _a_link(db, version)
    db.execute(
        text("UPDATE profile_links SET label = 'Portfolio' WHERE id = :id"), {"id": link_id}
    )
    db.execute(text("DELETE FROM profile_links WHERE id = :id"), {"id": link_id})
    assert (
        db.execute(text("SELECT count(*) FROM profile_links WHERE id = :id"), {"id": link_id}).scalar()
        == 0
    )


def test_a_link_cannot_name_a_project_of_another_version(db, make_version, confirm):
    """A draft's link pointing at a confirmed version's project would change what
    that frozen version shows beside its project."""
    confirmed = make_version(version_number=1)
    db.execute(
        text("INSERT INTO projects (profile_version_id, name) VALUES (:v, 'Ledger')"),
        {"v": confirmed.id},
    )
    project_id = db.execute(
        text("SELECT id FROM projects WHERE profile_version_id = :v"), {"v": confirmed.id}
    ).scalar()
    confirm(confirmed)
    draft = make_version(version_number=2)

    with pytest.raises(IntegrityError, match="another profile_version"):
        db.execute(
            text(
                "INSERT INTO profile_links (profile_version_id, url, owner_type, project_id) "
                "VALUES (:v, 'https://attacker.example', 'project', :p)"
            ),
            {"v": draft.id, "p": project_id},
        )
    db.rollback()


def test_a_link_may_name_a_project_of_its_own_version(db, make_version):
    draft = make_version()
    db.execute(
        text("INSERT INTO projects (profile_version_id, name) VALUES (:v, 'Ledger')"),
        {"v": draft.id},
    )
    project_id = db.execute(
        text("SELECT id FROM projects WHERE profile_version_id = :v"), {"v": draft.id}
    ).scalar()
    db.execute(
        text(
            "INSERT INTO profile_links (profile_version_id, url, owner_type, project_id) "
            "VALUES (:v, 'https://github.com/me/ledger', 'project', :p)"
        ),
        {"v": draft.id, "p": project_id},
    )
