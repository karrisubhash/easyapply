"""The job API, end to end over HTTP against a real database.

Ingestion is synchronous — see `app/services/job/pipeline.py` — so a single
POST reliably returns a job whose requirements are already attached. Nothing
here needs the queue-draining dance the resume tests use.
"""

import pytest

from tests.job_fixtures import make_job_payload, make_minimal_job_payload

OTHER = {"X-Dev-User-Email": "someone-else@example.com"}


def post_job(client, **overrides):
    return client.post("/api/v1/jobs", json=make_job_payload(**overrides))


# --- ingestion: the happy path -----------------------------------------------


def test_ingesting_a_job_normalizes_and_structures_it(client):
    response = post_job(client)
    assert response.status_code == 201

    body = response.json()
    assert body["job_title"] == "Senior Backend Engineer"
    assert body["company_name"] == "Acme Corp"
    # Free text from the source, normalized onto the closed vocabulary.
    assert body["employment_type"] == "full_time"
    assert body["workplace_type"] == "hybrid"
    assert body["salary_currency"] == "USD"
    assert body["salary_period"] == "year"
    assert body["latest_run"]["status"] == "completed"


def test_a_minimal_payload_is_accepted_with_everything_else_unknown(client):
    """Do not assume every site provides every field — unknown stays unknown,
    never fabricated."""
    response = client.post("/api/v1/jobs", json=make_minimal_job_payload())
    assert response.status_code == 201

    body = response.json()
    assert body["company_name"] is None
    assert body["location_raw"] is None
    assert body["employment_type"] is None
    assert body["workplace_type"] is None
    assert body["salary_min"] is None
    assert body["description_text"] is None
    assert body["latest_run"]["status"] == "completed"


def test_the_response_never_exposes_a_second_copy_of_the_ingestion_payload(client):
    """raw_payload is stored for audit but is not part of the API surface in
    this milestone — description_text already serves as the "raw" view."""
    body = post_job(client).json()
    assert "raw_payload" not in body


# --- identity and idempotency -------------------------------------------------


def test_the_same_identity_returns_the_existing_job_unchanged(client):
    first = post_job(client)
    second = post_job(client)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_resubmitting_with_different_text_does_not_overwrite_the_first_seen_version(client):
    """The documented policy: the first-seen version wins. A different
    description under the SAME identity is not merged in."""
    first = post_job(client, job_title="Senior Backend Engineer")
    second = post_job(client, job_title="COMPLETELY DIFFERENT TITLE")

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["job_title"] == "Senior Backend Engineer"


def test_tracking_parameters_do_not_defeat_deduplication(client):
    """Two fetches of the identical posting through different marketing links
    are the same job, not two."""
    first = post_job(
        client,
        external_job_id=None,
        source_url="https://www.linkedin.com/jobs/view/777?utm_source=email&utm_campaign=x",
    )
    second = post_job(
        client,
        external_job_id=None,
        source_url="https://www.linkedin.com/jobs/view/777?utm_source=twitter&ref=abc",
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_a_different_external_job_id_is_a_different_job_even_with_the_same_url_host(client):
    first = post_job(client, external_job_id="AAA")
    second = post_job(client, external_job_id="BBB", source_url="https://www.linkedin.com/jobs/view/999")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_the_same_job_on_two_sources_is_two_separate_rows(client):
    """Section 7: a real-world job may legitimately appear on multiple
    sources. Merging them would throw away which source each came from."""
    linkedin = post_job(client, source_type="linkedin", external_job_id="123456")
    indeed = post_job(
        client,
        source_type="indeed",
        external_job_id="123456",  # coincidentally the same external id
        source_url="https://www.indeed.com/viewjob?jk=123456",
    )

    assert linkedin.status_code == 201
    assert indeed.status_code == 201
    assert linkedin.json()["id"] != indeed.json()["id"]


def test_identical_content_from_two_users_is_two_jobs(client):
    """Scoped per user, like resume's content hash: a global index would leak
    that another user saved the same posting."""
    mine = post_job(client)
    theirs = post_job(client, external_job_id="mine-differs")
    theirs2 = client.post("/api/v1/jobs", json=make_job_payload(), headers=OTHER)

    assert mine.status_code == 201
    assert theirs2.status_code == 201
    assert mine.json()["id"] != theirs2.json()["id"]


# --- validation: malformed payloads are refused -------------------------------


def test_missing_job_title_is_refused(client):
    payload = make_job_payload()
    del payload["job_title"]
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_a_null_job_title_is_refused(client):
    response = client.post("/api/v1/jobs", json=make_job_payload(job_title=None))
    assert response.status_code == 422


def test_missing_source_url_is_refused(client):
    payload = make_job_payload()
    del payload["source_url"]
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_missing_source_type_is_refused(client):
    payload = make_job_payload()
    del payload["source_type"]
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422


def test_a_blank_job_title_is_refused(client):
    response = post_job(client, job_title="    ")
    assert response.status_code == 422


def test_an_unrecognized_source_type_is_refused(client):
    response = post_job(client, source_type="monster")
    assert response.status_code == 422


def test_a_non_http_url_scheme_is_refused(client):
    response = post_job(client, source_url="javascript:alert(1)")
    assert response.status_code == 422


def test_a_url_with_no_host_is_refused(client):
    response = post_job(client, source_url="https:///path")
    assert response.status_code == 422


def test_an_oversized_description_is_refused(client):
    response = post_job(client, description_text="x" * 50_001)
    assert response.status_code == 422


def test_a_description_at_exactly_the_limit_is_accepted(client):
    response = post_job(client, description_text="x" * 50_000, external_job_id="at-limit")
    assert response.status_code == 201


def test_an_unrecognized_field_is_refused(client):
    """extra="forbid": a payload naming a field we do not define is rejected
    outright, never silently ignored."""
    response = post_job(client, unexpected_field="anything")
    assert response.status_code == 422


# --- never trust the client for identity or status ----------------------------


def test_a_client_supplied_status_is_rejected_not_ignored(client):
    """The ingestion schema has no `status` field at all — this proves that
    structurally, not just that today's handler happens not to read one."""
    response = post_job(client, status="completed")
    assert response.status_code == 422


def test_a_client_supplied_user_id_is_rejected(client):
    response = post_job(client, user_id=999999)
    assert response.status_code == 422


def test_the_authenticated_user_comes_from_the_server_not_the_payload(client, db, user):
    """Even if a payload could somehow carry a user hint, CurrentUser is what
    decides — the same seam the resume and profile domains already trust."""
    response = post_job(client)
    assert response.status_code == 201

    listed = client.get("/api/v1/jobs").json()
    assert len(listed) == 1

    listed_as_other = client.get("/api/v1/jobs", headers=OTHER).json()
    assert listed_as_other == []


# --- retrieval -----------------------------------------------------------------


def test_listing_is_scoped_to_the_caller(client):
    post_job(client)
    assert client.get("/api/v1/jobs", headers=OTHER).json() == []


def test_reading_an_unknown_job_is_a_404(client):
    assert client.get("/api/v1/jobs/999999").status_code == 404


def test_another_user_cannot_read_my_job(client):
    job_id = post_job(client).json()["id"]
    assert client.get(f"/api/v1/jobs/{job_id}", headers=OTHER).status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}/requirements", headers=OTHER).status_code == 404


def test_the_detail_view_includes_the_description_and_canonical_url(client):
    job_id = post_job(client).json()["id"]
    body = client.get(f"/api/v1/jobs/{job_id}").json()

    assert body["description_text"] == make_job_payload()["description_text"]
    assert body["canonical_url"] == "https://www.linkedin.com/jobs/view/123456"
    assert body["external_job_id"] == "123456"


# --- requirements --------------------------------------------------------------


def test_requirements_carry_structured_data_with_provenance(client):
    job_id = post_job(client).json()["id"]
    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    assert body["run"]["status"] == "completed"
    candidate = body["requirements"]["candidate"]

    experience = candidate["required_experience"]
    assert any(item["minimum_years"] == 5.0 for item in experience)

    technologies = {t["name"]: t["is_required"] for t in candidate["technologies"]}
    assert technologies["Python"] is True
    assert technologies["Kubernetes"] is False

    # Every item's citation actually quotes the description.
    citation = experience[0]["provenance"][0]
    assert citation["quote"] in make_job_payload()["description_text"]
    assert citation["verified"] is True


def test_requirements_are_absent_but_not_an_error_with_no_description(client):
    job_id = client.post("/api/v1/jobs", json=make_minimal_job_payload()).json()["id"]
    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    assert body["run"]["status"] == "completed"
    candidate = body["requirements"]["candidate"]
    assert all(len(candidate[field]) == 0 for field in candidate)


def test_requirements_reflect_the_structurer_that_produced_them(client):
    job_id = post_job(client).json()["id"]
    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    assert body["requirements"]["structurer"] == "deterministic"
    assert body["requirements"]["provider"] is None
    assert body["requirements"]["schema_version"] == "1"


# --- reprocess -------------------------------------------------------------


def test_reprocessing_starts_a_new_attempt(client):
    job_id = post_job(client).json()["id"]
    response = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_run"]["attempt"] == 2
    assert body["latest_run"]["status"] == "completed"


def test_reprocessing_an_unknown_job_is_a_404(client):
    assert client.post("/api/v1/jobs/999999/reprocess").status_code == 404


def test_reprocessing_preserves_the_earlier_attempt(client, db):
    """Processing history is never overwritten — the same discipline resume
    reprocessing preserves."""
    from app.models import JobProcessingRun

    job_id = post_job(client).json()["id"]
    client.post(f"/api/v1/jobs/{job_id}/reprocess")

    runs = (
        db.query(JobProcessingRun)
        .filter_by(job_id=job_id)
        .order_by(JobProcessingRun.attempt)
        .all()
    )
    assert [(r.attempt, r.status) for r in runs] == [(1, "completed"), (2, "completed")]


def test_reprocessing_a_permanently_failed_job_is_refused(client, db):
    """A person retrying a failure the same job text will reproduce — the AI
    provider declined the content, for instance — gets told so rather than
    watching it fail again. Mirrors resume's own manual-retry refusal."""
    from datetime import datetime, timezone

    from app.models import JobProcessingRun

    job_id = post_job(client).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_content_rejected"
    run.error_message = "declined"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    response = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert response.status_code == 409
    # No second attempt was created.
    runs = db.query(JobProcessingRun).filter_by(job_id=job_id).all()
    assert len(runs) == 1


def test_reprocessing_a_transiently_failed_job_is_allowed(client, db):
    from datetime import datetime, timezone

    from app.models import JobProcessingRun

    job_id = post_job(client).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_timeout"
    run.error_message = "slow"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    response = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert response.status_code == 200
    assert response.json()["latest_run"]["attempt"] == 2


def test_a_permanently_failed_run_reports_it_cannot_be_retried(client, db):
    from datetime import datetime, timezone

    from app.models import JobProcessingRun

    job_id = post_job(client).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_content_rejected"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["latest_run"]["can_retry"] is False


def test_a_transiently_failed_run_reports_it_can_be_retried(client, db):
    from datetime import datetime, timezone

    from app.models import JobProcessingRun

    job_id = post_job(client).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_timeout"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["latest_run"]["can_retry"] is True


# --- the whole journey ---------------------------------------------------------


def test_full_ingestion_journey(client):
    """The acceptance path: extension payload -> validated -> normalized ->
    canonical job -> processing record -> ready for later matching."""
    response = post_job(client)
    assert response.status_code == 201
    job = response.json()

    requirements = client.get(f"/api/v1/jobs/{job['id']}/requirements").json()
    assert requirements["requirements"] is not None
    assert requirements["run"]["status"] == "completed"

    listing = client.get("/api/v1/jobs").json()
    assert len(listing) == 1
    assert listing[0]["id"] == job["id"]


def test_the_requirements_response_carries_provider_and_prompt_attribution(client, db):
    """The public API must expose which structurer, provider, model and prompt
    version produced a reading regardless of which one actually ran — the
    implementation provider is an internal concern, but auditability is not."""
    from app.models import JobProcessingRun, JobRequirements

    job_id = post_job(client).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    db.query(JobRequirements).filter_by(job_processing_run_id=run.id).update(
        {
            "structurer": "provider",
            "provider": "anthropic",
            "model": "claude-opus-5",
            "prompt_version": "2026-09-19.1",
        }
    )
    db.commit()

    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()
    assert body["requirements"]["structurer"] == "provider"
    assert body["requirements"]["provider"] == "anthropic"
    assert body["requirements"]["model"] == "claude-opus-5"
    assert body["requirements"]["prompt_version"] == "2026-09-19.1"
