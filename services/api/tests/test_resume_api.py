"""The resume API, end to end over HTTP against a real database.

Processing is asynchronous, so these tests are explicit about when it happens.
`upload()` returns as soon as the run is queued; `queue.drain()` is where the
worker runs. That separation is the point — a test that could not tell the two
apart could not tell whether the endpoint was still doing the work itself.
"""

import pytest

from app.core.config import settings
from tests.resume_fixtures import make_image_only_pdf, make_resume_docx, make_resume_pdf

OTHER = {"X-Dev-User-Email": "someone-else@example.com"}

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload(client, data=None, filename="resume.pdf", media=PDF_TYPE, headers=None):
    """Upload only. Nothing is processed until the queue is drained."""
    return client.post(
        "/api/v1/resumes",
        files={"file": (filename, data if data is not None else make_resume_pdf(), media)},
        headers=headers or {},
    )


def upload_and_process(client, queue, **kwargs):
    """Upload, then run the worker — the whole journey, deliberately sequenced."""
    response = upload(client, **kwargs)
    queue.drain()
    return response


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    """Every API test writes to its own throwaway storage root."""
    monkeypatch.setattr(settings, "RESUME_STORAGE_ROOT", str(tmp_path))


# --- upload ------------------------------------------------------------------


def test_uploading_a_pdf_queues_it_and_returns_immediately(client, queue):
    """The request's job is to make the work durable, not to do it."""
    response = upload(client)
    assert response.status_code == 201

    body = response.json()
    assert body["original_filename"] == "resume.pdf"
    assert body["content_type"] == PDF_TYPE
    # Queued, not processed: no stage has run and nothing has been extracted.
    assert body["latest_run"]["status"] == "uploaded"
    assert body["latest_run"]["attempt"] == 1
    assert body["latest_run"]["profile_version_id"] is None
    assert body["latest_run"]["started_at"] is None

    assert queue.enqueued == [body["latest_run"]["id"]]


def test_the_queued_run_becomes_a_draft_once_the_worker_runs(client, queue):
    response = upload(client)
    queue.drain()

    document_id = response.json()["id"]
    run = client.get(f"/api/v1/resumes/{document_id}").json()["latest_run"]
    assert run["status"] == "completed"
    assert run["profile_version_id"] is not None

    version = client.get("/api/v1/profile/versions/1").json()
    assert version["status"] == "draft"
    assert version["created_by_type"] == "ai"
    assert {skill["name"] for skill in version["snapshot"]["skills"]} >= {"Python", "Django"}


def test_uploading_a_docx_works_too(client, queue):
    response = upload_and_process(
        client, queue, data=make_resume_docx(), filename="resume.docx", media=DOCX_TYPE
    )
    assert response.status_code == 201

    document_id = response.json()["id"]
    run = client.get(f"/api/v1/resumes/{document_id}").json()["latest_run"]
    assert run["status"] == "completed"


def test_the_response_never_exposes_a_storage_path(client):
    body = upload(client).json()
    assert "storage_key" not in body
    assert "storage" not in str(body).lower()


def test_an_unsupported_file_is_refused(client):
    response = upload(client, data=b"just some text", filename="resume.txt", media="text/plain")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_file_type"


def test_a_lying_content_type_is_refused(client):
    response = upload(client, media=DOCX_TYPE)
    assert response.status_code == 422
    assert response.json()["code"] == "media_type_mismatch"


def test_a_traversing_filename_is_refused(client):
    response = upload(client, filename="../../etc/passwd.pdf")
    assert response.status_code == 422
    assert response.json()["code"] == "unsafe_filename"


def test_an_executable_disguised_as_a_pdf_is_refused(client):
    response = upload(client, data=b"MZ\x90\x00" + b"\x00" * 300)
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_file_type"


def test_an_oversized_upload_is_refused_at_the_edge(client, monkeypatch):
    monkeypatch.setattr(settings, "RESUME_MAX_UPLOAD_BYTES", 128)
    response = upload(client)

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"


# --- duplicates --------------------------------------------------------------


def test_the_same_bytes_return_the_same_document_with_200(client):
    """A retried request after a network timeout must not duplicate the resume."""
    first = upload(client)
    second = upload(client)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_a_duplicate_upload_does_not_create_a_second_draft(client, queue):
    upload(client)
    upload(client)
    queue.drain()

    versions = client.get("/api/v1/profile/versions").json()
    assert [version["version_number"] for version in versions] == [1]


def test_a_duplicate_upload_queues_no_second_run(client, queue):
    """Idempotency has to hold for the work as well as for the artifact.

    A client retrying after a timeout resolves to the same document, and the
    same document must not be processed twice — one resume, one run.
    """
    first = upload(client)
    second = upload(client)

    assert second.json()["id"] == first.json()["id"]
    assert len(queue.enqueued) == 1


def test_a_duplicate_upload_under_a_different_name_is_still_the_same_document(client):
    first = upload(client, filename="resume.pdf")
    second = upload(client, filename="my-cv.pdf")

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    # The stored document keeps the name it was first uploaded under.
    assert second.json()["original_filename"] == "resume.pdf"


def test_identical_content_from_two_users_is_two_documents(client):
    """Hash scope is per user; a global one would leak that a file exists."""
    mine = upload(client)
    theirs = upload(client, headers=OTHER)

    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine.json()["id"]


# --- retrieval ---------------------------------------------------------------


def test_listing_resumes(client, queue):
    upload_and_process(client, queue)
    response = client.get("/api/v1/resumes")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["latest_run"]["status"] == "completed"


def test_reading_a_resume_includes_its_stage_timings(client, queue):
    document_id = upload_and_process(client, queue).json()["id"]
    response = client.get(f"/api/v1/resumes/{document_id}")

    assert response.status_code == 200
    stages = {event["stage"] for event in response.json()["events"]}
    assert {"validate", "extract", "structure", "materialise"} <= stages


def test_reading_an_unknown_resume_is_a_404(client):
    assert client.get("/api/v1/resumes/9999").status_code == 404


def test_another_user_cannot_read_my_resume(client, queue):
    """The authorization boundary, such as it is before real auth exists."""
    document_id = upload_and_process(client, queue).json()["id"]

    assert client.get(f"/api/v1/resumes/{document_id}", headers=OTHER).status_code == 404
    assert client.get(f"/api/v1/resumes/{document_id}/result", headers=OTHER).status_code == 404


def test_listing_is_scoped_to_the_caller(client):
    upload(client)
    assert client.get("/api/v1/resumes", headers=OTHER).json() == []


# --- result ------------------------------------------------------------------


def test_the_result_carries_the_structured_reading_and_its_provenance(client, queue):
    document_id = upload_and_process(client, queue).json()["id"]
    response = client.get(f"/api/v1/resumes/{document_id}/result")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["result"]["structurer"] == "deterministic"
    assert body["result"]["profile_version_number"] == 1

    skills = body["result"]["candidate"]["skills"]
    assert skills
    citation = skills[0]["provenance"][0]
    assert citation["page"] >= 1
    assert citation["verified"] is True


def test_the_result_reports_pages_without_returning_their_text(client, queue):
    """Page text is the most sensitive thing we hold; the review screen does
    not need it, so the API does not ship it."""
    document_id = upload_and_process(client, queue).json()["id"]
    body = client.get(f"/api/v1/resumes/{document_id}/result").json()

    pages = body["result"]["pages"]
    assert [page["page_number"] for page in pages] == [1, 2]
    assert all("text" not in page for page in pages)


def test_a_failed_run_reports_its_stage_and_code(client, queue):
    """A scan with no OCR engine available on this machine."""
    response = upload_and_process(
        client, queue, data=make_image_only_pdf(1), filename="scan.pdf"
    )
    assert response.status_code == 201

    document_id = response.json()["id"]
    run = client.get(f"/api/v1/resumes/{document_id}").json()["latest_run"]
    assert run["status"] == "failed"
    assert run["failed_stage"] == "ocr"
    assert run["error_code"] == "ocr_unavailable"
    assert run["profile_version_id"] is None
    # Classified for the UI rather than derived by the client. A scan with no
    # OCR engine is never retried automatically — nothing changes on its own —
    # but the deployment, not the document, is what failed, so a person is
    # offered the retry for once an engine is installed. Refusing it wedged
    # the document for good: re-uploading the same bytes returns this one.
    assert run["is_terminal"] is True
    assert run["can_retry"] is True


def test_a_failed_run_creates_no_draft(client, queue):
    upload_and_process(client, queue, data=make_image_only_pdf(1), filename="scan.pdf")
    assert client.get("/api/v1/profile/versions/1").status_code == 404


# --- reprocess ---------------------------------------------------------------


def test_reprocessing_starts_a_new_attempt(client, queue):
    document_id = upload_and_process(client, queue).json()["id"]
    response = client.post(f"/api/v1/resumes/{document_id}/reprocess")

    assert response.status_code == 200
    body = response.json()
    # Queued like an upload: the endpoint starts an attempt, never runs it.
    assert body["latest_run"]["attempt"] == 2
    assert body["latest_run"]["status"] == "uploaded"
    assert len(queue.enqueued) == 2


def test_reprocessing_is_refused_while_a_run_is_in_flight(client, queue):
    """Two concurrent runs are what the lease exists to arbitrate; the API
    declines to create the situation in the first place."""
    document_id = upload(client).json()["id"]  # queued, never drained

    response = client.post(f"/api/v1/resumes/{document_id}/reprocess")
    assert response.status_code == 409
    assert "already being processed" in response.json()["detail"]


def test_reprocessing_is_refused_after_a_permanent_failure(client, queue):
    """A PDF that cannot be parsed fails the same way whatever is deployed."""
    document_id = upload_and_process(
        client, queue, data=b"%PDF-1.4\n" + b"\x00not a pdf" * 20, filename="broken.pdf"
    ).json()["id"]
    run = client.get(f"/api/v1/resumes/{document_id}").json()["latest_run"]
    assert run["error_code"] == "malformed_pdf"
    assert run["can_retry"] is False

    response = client.post(f"/api/v1/resumes/{document_id}/reprocess")
    assert response.status_code == 409
    assert "will not change" in response.json()["detail"]


def test_a_failure_the_deployment_caused_can_be_retried_by_hand(client, queue):
    """No OCR engine is the deployment's failure, not the document's.

    Before this, the reprocess endpoint refused it with the same 409 as a
    corrupt file — and a re-upload of the same bytes resolves to the same
    document — so once an engine was installed there was still no way to read
    the resume at all.
    """
    document_id = upload_and_process(
        client, queue, data=make_image_only_pdf(1), filename="scan.pdf"
    ).json()["id"]

    response = client.post(f"/api/v1/resumes/{document_id}/reprocess")
    assert response.status_code == 200
    assert response.json()["latest_run"]["attempt"] == 2


def test_reprocessing_an_unknown_resume_is_a_404(client):
    assert client.post("/api/v1/resumes/9999/reprocess").status_code == 404


# --- the whole journey -------------------------------------------------------


def test_upload_review_edit_and_confirm(client, queue):
    """The acceptance path: a resume becomes a confirmed, immutable version."""
    document_id = upload_and_process(client, queue).json()["id"]

    result = client.get(f"/api/v1/resumes/{document_id}/result").json()
    version_number = result["result"]["profile_version_number"]

    draft = client.get(f"/api/v1/profile/versions/{version_number}").json()
    assert draft["status"] == "draft"

    snapshot = draft["snapshot"]
    snapshot["skills"].append({"name": "Kubernetes"})
    edited = client.put(
        f"/api/v1/profile/versions/{version_number}/snapshot", json=snapshot
    )
    assert edited.status_code == 200
    assert "Kubernetes" in {skill["name"] for skill in edited.json()["snapshot"]["skills"]}

    confirmed = client.post(
        f"/api/v1/profile/versions/{version_number}/confirm", json={}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["is_current"] is True

    # And the existing immutability rules still apply to it.
    refused = client.put(f"/api/v1/profile/versions/{version_number}/snapshot", json=snapshot)
    assert refused.status_code == 409
