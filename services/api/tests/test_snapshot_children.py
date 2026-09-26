"""Constraints on the snapshot rows themselves."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Certification, Education, Experience, ProjectTechnology, Skill
from tests.factories import (
    a_certification,
    a_project,
    a_skill,
    an_education,
    an_experience,
)


# --- foreign keys and cascade ------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [an_experience, an_education, a_skill, a_project, a_certification],
    ids=["experience", "education", "skill", "project", "certification"],
)
def test_snapshot_rows_require_a_real_version(db, make_version, build):
    class Missing:
        id = 987654

    build(db, Missing(), flush=False)
    with pytest.raises(IntegrityError, match="profile_version_id"):
        db.flush()
    db.rollback()


def test_deleting_a_draft_version_cascades_to_every_child(db, make_version):
    version = make_version()
    an_experience(db, version)
    an_education(db, version)
    a_skill(db, version)
    a_certification(db, version)
    project = a_project(db, version)
    db.add(ProjectTechnology(project_id=project.id, name="FastAPI"))
    db.flush()

    db.delete(version)
    db.flush()

    for model in (Experience, Education, Skill, Certification):
        assert db.query(model).filter_by(profile_version_id=version.id).count() == 0
    assert db.query(ProjectTechnology).filter_by(project_id=project.id).count() == 0


# --- dates -------------------------------------------------------------------


def test_an_experience_cannot_end_before_it_starts(db, make_version):
    version = make_version()
    an_experience(
        db,
        version,
        start_date=date(2026, 5, 15),
        end_date=date(2026, 1, 1),
        end_date_precision="day",
        flush=False,
    )
    with pytest.raises(IntegrityError, match="ck_experiences_end_date_after_start_date"):
        db.flush()
    db.rollback()


def test_an_end_date_must_declare_its_precision(db, make_version):
    """A date stored without recording how precisely it was known turns a guess
    into a fact that no later migration can undo."""
    version = make_version()
    an_experience(db, version, end_date=date(2026, 8, 15), flush=False)
    with pytest.raises(IntegrityError, match="ck_experiences_end_date_precision_paired"):
        db.flush()
    db.rollback()


def test_a_precision_without_a_date_is_rejected(db, make_version):
    version = make_version()
    an_experience(db, version, end_date_precision="month", flush=False)
    with pytest.raises(IntegrityError, match="ck_experiences_end_date_precision_paired"):
        db.flush()
    db.rollback()


def test_precision_is_restricted_to_known_values(db, make_version):
    version = make_version()
    an_experience(db, version, start_date_precision="week", flush=False)
    with pytest.raises(IntegrityError, match="ck_experiences_start_date_precision"):
        db.flush()
    db.rollback()


def test_a_month_only_date_keeps_its_precision(db, make_version):
    version = make_version()
    experience = an_experience(
        db, version, start_date=date(2026, 5, 1), start_date_precision="month"
    )
    db.refresh(experience)
    assert experience.start_date_precision == "month"


def test_an_open_ended_experience_is_current(db, make_version):
    version = make_version()
    ongoing = an_experience(db, version, end_date=None)
    finished = an_experience(
        db, version, end_date=date(2026, 8, 15), end_date_precision="day"
    )
    assert ongoing.is_current is True
    assert finished.is_current is False


def test_employment_type_is_restricted_to_known_values(db, make_version):
    version = make_version()
    an_experience(db, version, employment_type="apprentice-ish", flush=False)
    with pytest.raises(IntegrityError, match="ck_experiences_employment_type"):
        db.flush()
    db.rollback()


def test_education_cannot_end_before_it_starts(db, make_version):
    version = make_version()
    an_education(
        db,
        version,
        start_date=date(2022, 8, 1),
        start_date_precision="month",
        end_date=date(2021, 8, 1),
        end_date_precision="month",
        flush=False,
    )
    with pytest.raises(IntegrityError, match="ck_education_end_date_after_start_date"):
        db.flush()
    db.rollback()


def test_education_dates_are_optional(db, make_version):
    version = make_version()
    record = an_education(
        db, version, degree="B.Tech", field_of_study="Mechanical Engineering"
    )
    assert record.start_date is None


# --- skills ------------------------------------------------------------------


def test_a_skill_cannot_be_listed_twice_in_one_version(db, make_version):
    version = make_version()
    a_skill(db, version, name="Python")
    a_skill(db, version, name="Python", flush=False)
    with pytest.raises(IntegrityError, match="uq_skills_profile_version_id_name_lower"):
        db.flush()
    db.rollback()


def test_skill_uniqueness_ignores_case(db, make_version):
    """Python and python are the same skill."""
    version = make_version()
    a_skill(db, version, name="Python")
    a_skill(db, version, name="python", flush=False)
    with pytest.raises(IntegrityError, match="uq_skills_profile_version_id_name_lower"):
        db.flush()
    db.rollback()


def test_the_same_skill_may_appear_in_different_versions(db, make_version):
    first = make_version(version_number=1)
    second = make_version(version_number=2)
    a_skill(db, first, name="Python")
    a_skill(db, second, name="Python")
    db.flush()  # no error


def test_proficiency_is_restricted_to_known_values(db, make_version):
    version = make_version()
    a_skill(db, version, proficiency="quite good", flush=False)
    with pytest.raises(IntegrityError, match="ck_skills_proficiency"):
        db.flush()
    db.rollback()


def test_years_of_experience_cannot_be_negative(db, make_version):
    version = make_version()
    a_skill(db, version, years_of_experience=Decimal("-1.0"), flush=False)
    with pytest.raises(IntegrityError, match="ck_skills_years_of_experience_not_negative"):
        db.flush()
    db.rollback()


def test_years_of_experience_keeps_half_years(db, make_version):
    version = make_version()
    skill = a_skill(db, version, name="Django", years_of_experience=Decimal("1.5"))
    db.refresh(skill)
    assert skill.years_of_experience == Decimal("1.5")


def test_skills_are_returned_in_a_stable_order(db, make_version):
    version = make_version()
    for name in ("PostgreSQL", "Django", "Python"):
        a_skill(db, version, name=name)
    db.expire(version)
    assert [s.name for s in version.skills] == ["Django", "PostgreSQL", "Python"]


# --- projects ----------------------------------------------------------------


def test_a_technology_cannot_be_listed_twice_on_one_project(db, make_version):
    version = make_version()
    project = a_project(db, version)
    db.add(ProjectTechnology(project_id=project.id, name="FastAPI"))
    db.flush()
    db.add(ProjectTechnology(project_id=project.id, name="FastAPI"))
    with pytest.raises(IntegrityError, match="project_technologies_pkey"):
        db.flush()
    db.rollback()


def test_deleting_a_project_removes_its_technologies(db, make_version):
    version = make_version()
    project = a_project(db, version)
    db.add(ProjectTechnology(project_id=project.id, name="FastAPI"))
    db.flush()

    db.delete(project)
    db.flush()
    assert db.query(ProjectTechnology).filter_by(project_id=project.id).count() == 0


# --- certifications ----------------------------------------------------------


def test_a_certification_cannot_expire_before_it_is_issued(db, make_version):
    version = make_version()
    a_certification(
        db,
        version,
        issue_date=date(2026, 5, 1),
        expiration_date=date(2025, 5, 1),
        flush=False,
    )
    with pytest.raises(IntegrityError, match="ck_certifications_expiration_after_issue"):
        db.flush()
    db.rollback()


def test_a_certification_need_not_expire(db, make_version):
    version = make_version()
    certification = a_certification(db, version, issue_date=date(2026, 5, 1))
    assert certification.expiration_date is None
