"""A reprocess that fails, and what the job looks like afterwards (J7).

Two defects:

  * The review screen's data source was `job.latest_run`. After a failed
    reprocess that run has no requirements, so `GET /jobs/{id}/requirements`
    reported nothing structured while matching and tailoring went on using the
    previous completed reading — two surfaces disagreeing about one job.
  * `require_retryable` refused a reprocess after ANY permanent failure,
    including the ones this deployment's configuration caused. A job that
    failed on `ai_billing_blocked` stayed unreprocessable after the account was
    topped up. Those codes are now `failures.CONFIGURATION_ERROR_CODES`.

The failing attempts are produced by the real pipeline, with the structurer
swapped for one that raises — not by writing a failed row by hand — so the
state under test is one the product can actually reach.
"""

import pytest

from app.services.job.failures import (
    CONFIGURATION_ERROR_CODES,
    PERMANENT_ERROR_CODES,
    classification,
    is_retryable,
    is_user_retryable,
)
from app.services.job.providers import ProviderError
from tests.job_fixtures import make_job_payload


class FailingStructurer:
    name = "failing"
    model = None
    provider = None

    def __init__(self, code: str) -> None:
        self.code = code

    def structure(self, job):
        raise ProviderError(self.code, "The AI provider refused the request.")


def fail_next_attempts_with(monkeypatch, code: str) -> None:
    from app.services.job import pipeline

    monkeypatch.setattr(pipeline, "get_job_structurer", lambda: FailingStructurer(code))


def restore_structurer(monkeypatch) -> None:
    """The deployment, fixed. Not `monkeypatch.undo()`, which would also undo
    anything a fixture patched on the same object."""
    from app.services.job import pipeline
    from app.services.job.structuring import get_job_structurer

    monkeypatch.setattr(pipeline, "get_job_structurer", get_job_structurer)


# --- the classification ---------------------------------------------------------


def test_configuration_codes_are_permanent_but_user_retryable():
    for code in CONFIGURATION_ERROR_CODES:
        assert code in PERMANENT_ERROR_CODES
        assert classification(code) == "permanent"
        # Never retried on its own — only when a person asks.
        assert is_retryable(code) is False
        assert is_user_retryable(code) is True


@pytest.mark.parametrize(
    "code", ["ai_content_rejected", "ai_request_invalid", "ai_output_invalid", "structuring_failed", "never_heard_of_it"]
)
def test_other_permanent_codes_stay_refused(code):
    assert is_user_retryable(code) is False


def test_transient_codes_remain_user_retryable():
    assert is_user_retryable("ai_timeout") is True
    assert is_user_retryable(None) is False


# --- the review screen's data source --------------------------------------------


def test_a_failed_reprocess_still_shows_the_last_completed_reading(client, db, monkeypatch):
    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]

    fail_next_attempts_with(monkeypatch, "ai_timeout")
    failed = client.post(f"/api/v1/jobs/{job_id}/reprocess")
    assert failed.status_code == 200
    assert failed.json()["latest_run"]["status"] == "failed"

    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    # The reading is still there, from the attempt that produced it ...
    assert body["requirements"] is not None
    assert body["requirements"]["candidate"]["technologies"]
    assert body["run"]["attempt"] == 1
    assert body["run"]["status"] == "completed"
    # ... and the failed attempt is still reported, not hidden.
    assert body["job"]["latest_run"]["attempt"] == 2
    assert body["job"]["latest_run"]["status"] == "failed"
    assert body["run"]["id"] != body["job"]["latest_run"]["id"]


def test_the_review_screen_and_matching_read_the_same_requirements_row(client, db, monkeypatch):
    """The disagreement itself: both must name one row."""
    from app.models import Job
    from app.services.job import service as job_service
    from app.services.matching.service import _load_requirements

    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    fail_next_attempts_with(monkeypatch, "ai_timeout")
    client.post(f"/api/v1/jobs/{job_id}/reprocess")

    db.expire_all()
    job = db.get(Job, job_id)
    shown = job_service.get_requirements_run(db, job=job)
    matched = _load_requirements(db, job=job)

    assert shown.requirements.id == matched.id


def test_a_job_whose_only_attempt_failed_still_reports_that_attempt(client, db):
    from datetime import datetime, timezone

    from app.models import JobProcessingRun, JobRequirements

    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    run = db.query(JobProcessingRun).filter_by(job_id=job_id).one()
    db.query(JobRequirements).filter_by(job_processing_run_id=run.id).delete()
    run.status = "failed"
    run.failed_stage = "structure"
    run.error_code = "ai_timeout"
    run.finished_at = datetime.now(timezone.utc)
    db.commit()

    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    assert body["requirements"] is None
    assert body["run"]["id"] == run.id
    assert body["run"]["status"] == "failed"


def test_a_later_successful_reprocess_replaces_the_reading(client, db, monkeypatch):
    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    fail_next_attempts_with(monkeypatch, "ai_timeout")
    client.post(f"/api/v1/jobs/{job_id}/reprocess")
    restore_structurer(monkeypatch)
    client.post(f"/api/v1/jobs/{job_id}/reprocess")

    body = client.get(f"/api/v1/jobs/{job_id}/requirements").json()

    assert body["run"]["attempt"] == 3
    assert body["job"]["latest_run"]["attempt"] == 3


# --- reprocess after a configuration failure -------------------------------------


@pytest.mark.parametrize("code", sorted(CONFIGURATION_ERROR_CODES))
def test_a_configuration_failure_can_be_reprocessed_once_it_is_fixed(client, db, monkeypatch, code):
    """The failure the deployment caused, then the deployment fixed (here: the
    structurer no longer raises), then the user asking again."""
    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    fail_next_attempts_with(monkeypatch, code)
    failed = client.post(f"/api/v1/jobs/{job_id}/reprocess").json()
    assert failed["latest_run"]["error_code"] == code
    # The button is offered, and the endpoint behind it agrees.
    assert failed["latest_run"]["can_retry"] is True

    restore_structurer(monkeypatch)
    response = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert response.status_code == 200
    assert response.json()["latest_run"]["attempt"] == 3
    assert response.json()["latest_run"]["status"] == "completed"


def test_a_configuration_failure_retried_unfixed_fails_the_same_way(client, db, monkeypatch):
    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    fail_next_attempts_with(monkeypatch, "ai_billing_blocked")
    client.post(f"/api/v1/jobs/{job_id}/reprocess")

    again = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert again.status_code == 200
    assert again.json()["latest_run"]["attempt"] == 3
    assert again.json()["latest_run"]["error_code"] == "ai_billing_blocked"


@pytest.mark.parametrize("code", ["ai_content_rejected", "ai_request_invalid", "ai_output_invalid"])
def test_a_non_configuration_permanent_failure_is_still_refused(client, db, monkeypatch, code):
    job_id = client.post("/api/v1/jobs", json=make_job_payload()).json()["id"]
    fail_next_attempts_with(monkeypatch, code)
    failed = client.post(f"/api/v1/jobs/{job_id}/reprocess").json()
    assert failed["latest_run"]["can_retry"] is False

    restore_structurer(monkeypatch)
    response = client.post(f"/api/v1/jobs/{job_id}/reprocess")

    assert response.status_code == 409
