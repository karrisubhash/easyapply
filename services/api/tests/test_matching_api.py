"""The match API, end to end over HTTP against a real database.

The routes are thin, so what these tests are really about is the contract at
the boundary: the status codes that distinguish a new evaluation from a
repeated one, the fields a client is allowed to send, and — the part no
frontend may be trusted with — that every query is scoped to the caller.

`X-Dev-User-Email` is the one seam identity runs through (see
`app/api/dev_identity.py`), which is what lets one test act as two people.
"""

from datetime import date

import pytest

from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from tests.matching_fixtures import (
    a_candidate,
    a_structured_job,
    persisted_profile_version,
)

#: The account `current_user` provisions when no header is sent.
DEV_EMAIL = "dev@easyapply.local"
OTHER = {"X-Dev-User-Email": "someone-else@example.com"}


@pytest.fixture
def caller(db):
    """The user the client acts as by default, provisioned up front so
    fixtures can hang rows off it before the first request."""
    from app.models import User

    account = User(email=DEV_EMAIL)
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def version(db, caller):
    return persisted_profile_version(
        db,
        caller,
        skills=[("Python", 6.0), ("PostgreSQL", None)],
        experiences=[
            {
                "job_title": "Backend Developer",
                "start": date(2019, 1, 1),
                "end": date(2024, 1, 1),
                "description": "Django REST API development",
            }
        ],
        education=[{"degree": "BSc Computer Science", "field_of_study": "Computer Science"}],
        certifications=["AWS Certified Developer"],
    )


@pytest.fixture
def job(db, caller):
    job_row, _ = a_structured_job(
        db,
        caller,
        candidate=a_candidate(
            required_skills=["Python", "Rust"],
            preferred_skills=["Kubernetes"],
            required_experience=[("3+ years of Python", 3.0)],
            education=[("Bachelor's degree in Computer Science", "bachelor", None, True)],
            certifications=[("AWS Certified Developer", True)],
            qualifications=[("Must be authorized to work in the US", True)],
        ),
    )
    return job_row


# --- creating ----------------------------------------------------------------


def test_creating_a_match_returns_201_with_the_full_result(client, version, job):
    response = client.post(f"/api/v1/jobs/{job.id}/matches", json={})
    assert response.status_code == 201

    body = response.json()
    assert body["profile_version_id"] == version.id
    assert body["job_id"] == job.id
    assert body["algorithm_version"] == "matching_v5_1"
    assert body["requirement_results"]


def test_the_response_leads_with_counts_rather_than_a_single_number(client, version, job):
    """§3 and §12: a match is not "87%". The counts and the verdicts are the
    result; the score is one field among them."""
    body = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    for field in (
        "required_total",
        "required_matched",
        "required_missing",
        "required_uncertain",
        "preferred_total",
        "preferred_matched",
        "preferred_missing",
        "preferred_uncertain",
        "required_coverage",
        "preferred_coverage",
        "overall_score",
    ):
        assert field in body


def test_every_requirement_result_carries_its_explanation(client, version, job):
    body = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    for result in body["requirement_results"]:
        assert result["explanation"]
        assert result["status"] in {STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN}
        assert result["category"]
        assert isinstance(result["is_required"], bool)


def test_a_matched_result_carries_evidence_over_the_wire(client, version, job):
    """§5: the evidence is the useful part, so it has to survive
    serialization, not merely exist in the database."""
    body = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    matched = [r for r in body["requirement_results"] if r["status"] == STATUS_MATCHED]
    assert matched
    for result in matched:
        assert result["evidence"]
        assert result["evidence"][0]["label"]
        assert result["evidence"][0]["source_type"]


def test_a_missing_result_carries_no_evidence_and_says_why(client, version, job):
    body = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    missing = [r for r in body["requirement_results"] if r["status"] == STATUS_MISSING]
    assert missing
    for result in missing:
        assert result["evidence"] == []
        assert "no qualifying evidence was found" in result["explanation"].lower()


def test_a_specific_profile_version_can_be_named(client, db, caller, version, job):
    second = persisted_profile_version(
        db, caller, skills=[("Rust", None)], version_number=2
    )

    response = client.post(
        f"/api/v1/jobs/{job.id}/matches", json={"profile_version_id": version.id}
    )

    assert response.status_code == 201
    assert response.json()["profile_version_id"] == version.id


def test_unknown_fields_are_refused(client, version, job):
    """`extra="forbid"`: a client that sends `algorithm_version` or `user_id`
    must be told it was ignored, not have it silently dropped."""
    response = client.post(
        f"/api/v1/jobs/{job.id}/matches",
        json={"profile_version_id": version.id, "algorithm_version": "matching_v9"},
    )
    assert response.status_code == 422


def test_the_client_cannot_choose_the_algorithm_or_the_owner(client, version, job):
    for payload in (
        {"user_id": 99},
        {"overall_score": 1.0},
        {"as_of_date": "2030-01-01"},
    ):
        assert client.post(f"/api/v1/jobs/{job.id}/matches", json=payload).status_code == 422


def test_has_unmet_required_is_derived_on_the_server(client, version, job):
    """§13's phrasing: it says what the counts say, and never that a
    candidate is unsuitable."""
    body = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    assert body["has_unmet_required"] == (
        body["required_missing"] > 0 or body["required_uncertain"] > 0
    )


# --- idempotency over HTTP ----------------------------------------------------


def test_a_repeated_request_answers_200_with_the_same_result(client, version, job):
    """§18: a client retrying after a timeout must not create a second copy
    of the same judgment."""
    first = client.post(f"/api/v1/jobs/{job.id}/matches", json={})
    second = client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_repeated_requests_do_not_accumulate_matches(client, version, job):
    for _ in range(4):
        client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    listed = client.get(f"/api/v1/jobs/{job.id}/matches").json()
    assert len(listed) == 1


# --- conflicts ----------------------------------------------------------------


def test_matching_a_draft_version_is_a_409(client, db, caller, job):
    draft = persisted_profile_version(
        db, caller, skills=[("Python", None)], confirmed=False, is_current=False
    )

    response = client.post(
        f"/api/v1/jobs/{job.id}/matches", json={"profile_version_id": draft.id}
    )

    assert response.status_code == 409
    assert "confirmed" in response.json()["detail"].lower()


def test_matching_without_a_confirmed_version_is_a_409(client, db, caller, job):
    response = client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    assert response.status_code == 409
    assert "confirm" in response.json()["detail"].lower()


def test_matching_an_unstructured_job_is_a_409(client, db, caller, version):
    from app.models import Job

    job = Job(
        user_id=caller.id,
        source_type="linkedin",
        source_url="https://www.linkedin.com/jobs/view/raw",
        canonical_url="https://www.linkedin.com/jobs/view/raw",
        external_job_id="raw",
        content_hash="c" * 64,
        job_title="Engineer",
        company_name="Acme",
        description_text="Requirements",
        raw_payload={},
    )
    db.add(job)
    db.flush()

    response = client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    assert response.status_code == 409
    assert "structured" in response.json()["detail"].lower()


def test_matching_a_job_that_does_not_exist_is_a_404(client, version):
    assert client.post("/api/v1/jobs/999999/matches", json={}).status_code == 404


# --- reading ------------------------------------------------------------------


def test_a_match_can_be_fetched_by_its_own_id(client, version, job):
    created = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    response = client.get(f"/api/v1/matches/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["requirement_results"]


def test_listing_a_jobs_matches_returns_summaries(client, version, job):
    client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    body = client.get(f"/api/v1/jobs/{job.id}/matches").json()

    assert len(body) == 1
    assert "required_matched" in body[0]
    # A listing is counts, not verdicts: the detail endpoint carries those.
    assert "requirement_results" not in body[0]


def test_listing_a_job_with_no_matches_is_empty(client, version, job):
    assert client.get(f"/api/v1/jobs/{job.id}/matches").json() == []


def test_a_match_that_does_not_exist_is_a_404(client):
    assert client.get("/api/v1/matches/999999").status_code == 404


# --- ownership (§19) ----------------------------------------------------------


def test_another_user_cannot_read_this_match(client, version, job):
    created = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    response = client.get(f"/api/v1/matches/{created['id']}", headers=OTHER)

    assert response.status_code == 404


def test_another_user_cannot_list_matches_for_this_job(client, version, job):
    client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    response = client.get(f"/api/v1/jobs/{job.id}/matches", headers=OTHER)

    assert response.status_code == 404


def test_another_user_cannot_match_against_this_job(client, version, job):
    response = client.post(f"/api/v1/jobs/{job.id}/matches", json={}, headers=OTHER)

    assert response.status_code == 404


def test_another_user_cannot_name_this_profile_version(client, db, version, job):
    """The insecure-direct-object-reference: a version id is a global primary
    key, so the stranger's own job plus this version must still be refused."""
    from tests.matching_fixtures import another_user

    stranger = another_user(db, email="someone-else@example.com")
    stranger_job, _ = a_structured_job(db, stranger, external_job_id="stranger")

    response = client.post(
        f"/api/v1/jobs/{stranger_job.id}/matches",
        json={"profile_version_id": version.id},
        headers=OTHER,
    )

    assert response.status_code == 404


def test_a_404_does_not_confirm_that_the_other_users_resource_exists(client, version, job):
    """A stranger's job id and an id that was never issued must be
    indistinguishable from outside."""
    real = client.get(f"/api/v1/jobs/{job.id}/matches", headers=OTHER)
    invented = client.get("/api/v1/jobs/999999/matches", headers=OTHER)

    assert real.status_code == invented.status_code == 404
    assert real.json()["detail"] == invented.json()["detail"]


# --- which version was judged, as the user names it -----------------------------
#
# Found by opening the match screen in a browser against the real development
# database. The footer read "Profile version 2" for a match that had been run
# against version 1: the response carried `profile_version_id` and the screen
# printed it under that label.
#
# The two numbers are equal on a freshly created database, so the unit suite,
# the browser suite and every demo agreed the screen was right. They stop being
# equal the moment a profile has more than one version — the real one has
# twelve, where row id 17 is version 7 and row id 22 is version 12.
#
# It matters more than a cosmetic mislabel because it is the one number on that
# page a user checks against something they did themselves: they confirmed a
# version, and this says which version was judged.


def test_the_match_names_the_version_number_not_the_row_id(client, db, caller, job):
    """The response carries both, and they are different numbers here."""
    first = persisted_profile_version(
        db, caller, skills=[("Python", 6.0)], version_number=1
    )
    second = persisted_profile_version(
        db,
        caller,
        skills=[("Python", 6.0), ("PostgreSQL", None)],
        version_number=2,
        is_current=True,
        profile=first.profile,
    )
    db.commit()
    assert second.id != second.version_number, (
        "this test is meaningless unless the row id and the version number differ"
    )

    response = client.post(
        f"/api/v1/jobs/{job.id}/matches",
        json={"profile_version_id": second.id},
    )
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["profile_version_id"] == second.id
    assert body["profile_version_number"] == second.version_number


def test_the_listing_carries_the_version_number_too(client, db, caller, job, version):
    """The listing is serialised a page at a time, so the number has to survive
    the summary schema as well as the detail one — and without a query per row."""
    client.post(f"/api/v1/jobs/{job.id}/matches", json={})

    response = client.get(f"/api/v1/jobs/{job.id}/matches")
    assert response.status_code == 200

    rows = response.json()
    assert rows
    for row in rows:
        assert row["profile_version_number"] == version.version_number
