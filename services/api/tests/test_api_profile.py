"""Integration tests for the Profile API, against real PostgreSQL.

Nothing is mocked. Every request runs the real router, the real service layer,
the real triggers and the real error mapping; only the session is overridden,
so that the test's savepoint can throw the work away afterwards.
"""

from typing import get_args

import pytest

from app.models.date_precision import PRECISION_VALUES
from app.models.experience import EMPLOYMENT_TYPES
from app.models.skill import PROFICIENCY_LEVELS
from app.models.profile_version import (
    CREATED_BY_AI,
    CREATED_BY_SYSTEM,
    CREATED_BY_USER,
    STATUS_CONFIRMED,
    STATUS_DRAFT,
)
from app.schemas.profile import CreatedByType, VersionStatus
from app.schemas.snapshot import DatePrecision, EmploymentType, Proficiency

OTHER = {"X-Dev-User-Email": "someone-else@example.com"}


def a_snapshot(**overrides):
    snapshot = {
        "experiences": [
            {
                "company": "APTRANSCO",
                "job_title": "AI Developer Intern",
                "employment_type": "internship",
                "start_date": "2026-05-15",
                "start_date_precision": "day",
                "end_date": "2026-08-15",
                "end_date_precision": "day",
            }
        ],
        "education": [
            {
                "institution": "Anil Neerukonda Institute of Technology & Sciences",
                "degree": "B.Tech",
                "field_of_study": "Mechanical Engineering",
            }
        ],
        "skills": [
            {"name": "Python", "category": "Programming", "proficiency": "advanced"},
            {"name": "Django", "category": "Backend", "proficiency": "intermediate"},
        ],
        "projects": [
            {"name": "EasyApply", "technologies": ["FastAPI", "PostgreSQL"]}
        ],
        "certifications": [{"name": "AZ-900", "issuing_organization": "Microsoft"}],
    }
    snapshot.update(overrides)
    return snapshot


@pytest.fixture
def profile_client(client):
    """A client whose caller already has a profile."""
    assert client.post("/api/v1/profile").status_code == 201
    return client


# --- profile -----------------------------------------------------------------


def test_create_profile(client):
    response = client.post("/api/v1/profile")
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["created_at"].endswith(("Z", "+00:00")) or "T" in body["created_at"]


def test_creating_a_second_profile_conflicts(profile_client):
    response = profile_client.post("/api/v1/profile")
    assert response.status_code == 409
    assert response.json()["code"] == "uq_profiles_user_id"


def test_get_profile(profile_client):
    response = profile_client.get("/api/v1/profile")
    assert response.status_code == 200
    assert response.json()["id"] > 0


def test_get_profile_before_creating_one(client):
    assert client.get("/api/v1/profile").status_code == 404


def test_each_caller_gets_their_own_profile(profile_client):
    mine = profile_client.get("/api/v1/profile").json()["id"]
    assert profile_client.get("/api/v1/profile", headers=OTHER).status_code == 404

    theirs = profile_client.post("/api/v1/profile", headers=OTHER).json()["id"]
    assert theirs != mine
    assert profile_client.get("/api/v1/profile").json()["id"] == mine


# --- draft versions ----------------------------------------------------------


def test_create_draft_version(profile_client):
    response = profile_client.post("/api/v1/profile/versions")
    assert response.status_code == 201
    body = response.json()
    assert body["version_number"] == 1
    assert body["status"] == "draft"
    assert body["is_current"] is False
    assert body["confirmed_at"] is None
    assert body["snapshot"]["skills"] == []


def test_version_numbers_increment(profile_client):
    numbers = [
        profile_client.post("/api/v1/profile/versions").json()["version_number"]
        for _ in range(3)
    ]
    assert numbers == [1, 2, 3]


def test_version_numbers_are_per_profile(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile", headers=OTHER)
    theirs = profile_client.post("/api/v1/profile/versions", headers=OTHER).json()
    assert theirs["version_number"] == 1


def test_creating_a_version_without_a_profile(client):
    assert client.post("/api/v1/profile/versions").status_code == 404


def test_list_versions(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.get("/api/v1/profile/versions")
    assert response.status_code == 200
    assert [v["version_number"] for v in response.json()] == [1, 2]
    assert "snapshot" not in response.json()[0]


def test_get_unknown_version(profile_client):
    assert profile_client.get("/api/v1/profile/versions/99").status_code == 404


def test_another_caller_cannot_read_my_version(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile", headers=OTHER)
    assert profile_client.get("/api/v1/profile/versions/1", headers=OTHER).status_code == 404


# --- snapshot ----------------------------------------------------------------


def test_replace_snapshot_round_trips(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    assert response.status_code == 200

    snapshot = response.json()["snapshot"]
    assert sorted(s["name"] for s in snapshot["skills"]) == ["Django", "Python"]
    assert snapshot["experiences"][0]["company"] == "APTRANSCO"
    assert snapshot["projects"][0]["technologies"] == ["FastAPI", "PostgreSQL"]
    assert snapshot["certifications"][0]["name"] == "AZ-900"

    fetched = profile_client.get("/api/v1/profile/versions/1").json()["snapshot"]
    assert fetched == snapshot


def test_replace_snapshot_is_idempotent(profile_client):
    profile_client.post("/api/v1/profile/versions")
    first = profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    second = profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    assert second.status_code == 200
    assert first.json()["snapshot"] == second.json()["snapshot"]


def test_replace_snapshot_fully_replaces(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(skills=[{"name": "Rust"}], experiences=[], projects=[]),
    )
    snapshot = response.json()["snapshot"]
    assert [s["name"] for s in snapshot["skills"]] == ["Rust"]
    assert snapshot["experiences"] == []
    assert snapshot["projects"] == []


def test_an_empty_snapshot_is_accepted(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put("/api/v1/profile/versions/1/snapshot", json={})
    assert response.status_code == 200
    assert response.json()["snapshot"]["skills"] == []


def test_snapshot_on_unknown_version(profile_client):
    assert (
        profile_client.put("/api/v1/profile/versions/9/snapshot", json=a_snapshot()).status_code
        == 404
    )


# --- database invariants surface as API errors -------------------------------


def test_duplicate_skill_names_are_rejected_by_the_database(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(skills=[{"name": "Python"}, {"name": "python"}]),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "uq_skills_profile_version_id_name_lower"


def test_a_date_without_its_precision_is_rejected(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(
            education=[{"institution": "ANITS", "start_date": "2022-08-01"}]
        ),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ck_education_start_date_precision_paired"


def test_an_experience_ending_before_it_starts_is_rejected(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(
            experiences=[
                {
                    "company": "X",
                    "job_title": "Y",
                    "start_date": "2026-05-15",
                    "start_date_precision": "day",
                    "end_date": "2026-01-01",
                    "end_date_precision": "day",
                }
            ]
        ),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ck_experiences_end_date_after_start_date"


def test_an_unknown_employment_type_is_rejected_by_pydantic(profile_client):
    """Shape errors never reach the database."""
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(
            experiences=[
                {
                    "company": "X",
                    "job_title": "Y",
                    "employment_type": "apprentice-ish",
                    "start_date": "2026-05-15",
                    "start_date_precision": "day",
                }
            ]
        ),
    )
    assert response.status_code == 422


def test_unknown_fields_are_refused(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(skills=[{"name": "Python", "favourite": True}]),
    )
    assert response.status_code == 422


# --- confirmation and immutability -------------------------------------------


def test_confirm_a_draft(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    response = profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "confirmed"
    assert body["is_current"] is True
    assert body["confirmed_at"] is not None
    assert len(body["snapshot"]["skills"]) == 2


def test_confirm_without_promoting(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.post(
        "/api/v1/profile/versions/1/confirm", json={"make_current": False}
    )
    assert response.json()["is_current"] is False
    assert profile_client.get("/api/v1/profile/versions/current").status_code == 404


def test_a_version_confirmed_without_promoting_can_be_made_current_later(profile_client):
    """Unticking "make current" used to be a one-way door: frozen, never current."""
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={"make_current": False})

    response = profile_client.post("/api/v1/profile/versions/1/make-current")
    assert response.status_code == 200
    assert response.json()["is_current"] is True
    assert profile_client.get("/api/v1/profile/versions/current").json()["version_number"] == 1


def test_making_a_version_current_demotes_the_one_before_it(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/2/confirm", json={"make_current": False})

    profile_client.post("/api/v1/profile/versions/2/make-current")

    versions = {v["version_number"]: v["is_current"] for v in profile_client.get("/api/v1/profile/versions").json()}
    assert versions == {1: False, 2: True}


def test_making_the_current_version_current_is_a_no_op(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    response = profile_client.post("/api/v1/profile/versions/1/make-current")
    assert response.status_code == 200
    assert response.json()["is_current"] is True


def test_a_draft_cannot_be_made_current(profile_client):
    profile_client.post("/api/v1/profile/versions")
    response = profile_client.post("/api/v1/profile/versions/1/make-current")
    assert response.status_code == 409
    assert "confirm it" in response.json()["detail"].lower()


def test_another_caller_cannot_make_my_version_current(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={"make_current": False})
    profile_client.post("/api/v1/profile", headers=OTHER)
    assert (
        profile_client.post("/api/v1/profile/versions/1/make-current", headers=OTHER).status_code
        == 404
    )
    assert profile_client.get("/api/v1/profile/versions/current").status_code == 404


def test_a_confirmed_version_cannot_be_edited(profile_client):
    """Requirement 14, through the only endpoint that writes snapshot rows."""
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})

    response = profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    assert response.status_code == 409
    assert "confirmed" in response.json()["detail"].lower()


def test_a_confirmed_version_cannot_be_confirmed_again(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    response = profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    assert response.status_code == 409


def test_the_session_survives_a_rejected_request(profile_client):
    """A 409 must not poison the session for the next request."""
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})
    assert profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot()).status_code == 409

    following = profile_client.post("/api/v1/profile/versions")
    assert following.status_code == 201
    assert following.json()["version_number"] == 2


# --- current-version switching -----------------------------------------------


def test_promoting_a_new_version_demotes_the_old_one(profile_client):
    """The partial unique index and its ordering requirement, through the API."""
    profile_client.post("/api/v1/profile/versions")
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})

    profile_client.post("/api/v1/profile/versions")
    second = profile_client.post("/api/v1/profile/versions/2/confirm", json={})
    assert second.status_code == 200
    assert second.json()["is_current"] is True

    assert profile_client.get("/api/v1/profile/versions/1").json()["is_current"] is False
    assert profile_client.get("/api/v1/profile/versions/current").json()["version_number"] == 2


def test_current_version_returns_the_snapshot(profile_client):
    profile_client.post("/api/v1/profile/versions")
    profile_client.put("/api/v1/profile/versions/1/snapshot", json=a_snapshot())
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})

    current = profile_client.get("/api/v1/profile/versions/current").json()
    assert current["version_number"] == 1
    assert sorted(s["name"] for s in current["snapshot"]["skills"]) == ["Django", "Python"]


def test_no_current_version_yet(profile_client):
    profile_client.post("/api/v1/profile/versions")
    assert profile_client.get("/api/v1/profile/versions/current").status_code == 404


# --- historical reconstruction through the API -------------------------------


def test_an_earlier_version_is_untouched_by_a_later_one(profile_client):
    """The architecture's own example, end to end over HTTP."""
    profile_client.post("/api/v1/profile/versions")
    profile_client.put(
        "/api/v1/profile/versions/1/snapshot",
        json=a_snapshot(
            skills=[{"name": "Python"}, {"name": "Django"}, {"name": "PostgreSQL"}]
        ),
    )
    profile_client.post("/api/v1/profile/versions/1/confirm", json={})

    profile_client.post("/api/v1/profile/versions")
    profile_client.put(
        "/api/v1/profile/versions/2/snapshot",
        json=a_snapshot(
            skills=[
                {"name": "Python"},
                {"name": "Django"},
                {"name": "PostgreSQL"},
                {"name": "FastAPI"},
            ]
        ),
    )
    profile_client.post("/api/v1/profile/versions/2/confirm", json={})

    v1 = profile_client.get("/api/v1/profile/versions/1").json()
    v2 = profile_client.get("/api/v1/profile/versions/2").json()

    assert sorted(s["name"] for s in v1["snapshot"]["skills"]) == [
        "Django",
        "PostgreSQL",
        "Python",
    ]
    assert len(v2["snapshot"]["skills"]) == 4
    assert v1["is_current"] is False and v2["is_current"] is True


# --- the schema literals cannot drift from the database ----------------------


def test_literals_match_the_database_value_sets():
    assert set(get_args(DatePrecision)) == set(PRECISION_VALUES)
    assert set(get_args(EmploymentType)) == set(EMPLOYMENT_TYPES)
    assert set(get_args(Proficiency)) == set(PROFICIENCY_LEVELS)
    assert set(get_args(VersionStatus)) == {STATUS_DRAFT, STATUS_CONFIRMED}
    assert set(get_args(CreatedByType)) == {
        CREATED_BY_USER,
        CREATED_BY_AI,
        CREATED_BY_SYSTEM,
    }
