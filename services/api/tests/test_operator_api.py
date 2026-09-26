"""The internal operator API.

Two things are being asserted throughout. That the six states are genuinely
distinguishable — an operator sent to look at a provider outage when the real
answer is "someone uploaded a photograph" has been actively misled. And that a
surface reading across ALL users leaks nothing: no filenames, no page text, no
structured payloads, no email addresses, no storage keys.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models import ResumeDocument, ResumeProcessingRun, User
from app.services.resume import lease, operations, retry, service
from app.services.resume.queue import ExternalQueue
from app.services.storage import InMemoryStorage
from tests.conftest import OPERATOR_TEST_TOKEN
from tests.resume_fixtures import make_image_only_pdf, make_resume_pdf

BASE = "/api/v1/internal/resume-processing"


@pytest.fixture
def client(client):
    """The shared client, presenting the operator token the suite is pinned to.

    The boundary refuses every request when no token is configured, so there
    is no longer an "open" posture to test the endpoints under: every request
    here authenticates the way a configured deployment requires. A test about
    a missing or wrong token removes or overrides the header itself.
    """
    client.headers["X-Operator-Token"] = OPERATOR_TEST_TOKEN
    return client


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    """One throwaway storage root per test, shared by the uploader and the worker.

    The worker resolves storage itself, so a test that uploaded into a private
    InMemoryStorage would have every run fail at `validate` with
    stored_object_missing — technically a failure, but not the one under test.
    """
    monkeypatch.setattr(settings, "RESUME_STORAGE_ROOT", str(tmp_path))


@pytest.fixture
def storage():
    """Only for tests that never run the pipeline."""
    return InMemoryStorage()


def upload(db, user, _storage=None, data=None, filename="resume.pdf"):
    document, _created = service.upload_resume(
        db,
        user_id=user.id,
        filename=filename,
        declared_media_type="application/pdf",
        data=data if data is not None else make_resume_pdf(),
    )
    return document


def fail(db, run, code, stage="structure"):
    run.status = "failed"
    run.failed_stage = stage
    run.error_code = code
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    return run


# --- the authorization boundary ----------------------------------------------


def test_the_operator_api_answers_a_request_carrying_the_configured_token(client):
    assert client.get(f"{BASE}/attention").status_code == 200


def test_no_configured_token_closes_the_boundary_rather_than_opening_it(
    client, monkeypatch
):
    """Unset used to mean open. It now means nobody, whatever is presented."""
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", None)

    response = client.get(f"{BASE}/attention")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"

    client.headers.pop("X-Operator-Token")
    assert client.get(f"{BASE}/attention").status_code == 403

    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", "")
    assert client.get(f"{BASE}/attention").status_code == 403


def test_no_configured_token_leaves_development_open_and_says_so(client, monkeypatch):
    """A developer's own machine keeps the posture it always had: open, loud."""
    from app.api import operator_identity

    warned: list[str] = []
    monkeypatch.setattr(
        operator_identity.logger, "warning", lambda message, *a, **k: warned.append(message)
    )
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", None)
    client.headers.pop("X-Operator-Token")

    assert client.get(f"{BASE}/attention").status_code == 200
    assert warned == ["operator.unauthenticated_access"]


def test_no_configured_token_closes_production(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", None)
    client.headers.pop("X-Operator-Token")

    assert client.get(f"{BASE}/attention").status_code == 403


def test_a_non_ascii_token_is_refused_rather_than_crashing(client):
    """`compare_digest` raises TypeError on a non-ASCII str; that was a 500."""
    for presented in ("tökén".encode("utf-8"), "tökén".encode("latin-1"), b"\xff\xfe"):
        response = client.get(
            f"{BASE}/attention", headers={"X-Operator-Token": presented}
        )
        assert response.status_code == 403, presented
        assert response.json()["code"] == "forbidden"


def test_a_non_ascii_configured_token_is_compared_without_crashing(monkeypatch):
    """Both sides non-ASCII: still a comparison, not a TypeError.

    Called directly rather than through the TestClient, which re-encodes header
    bytes on the way in and so cannot say which bytes the server received.
    """
    from app.api.operator_identity import require_operator
    from app.services.errors import ForbiddenError

    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", "tökén-s3cret")

    assert require_operator(x_operator_token="tökén-s3cret").authenticated is True
    with pytest.raises(ForbiddenError):
        require_operator(x_operator_token="tökén-s3creT")


def test_a_configured_token_is_required(client, monkeypatch):
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", "s3cret")
    client.headers.pop("X-Operator-Token")

    assert client.get(f"{BASE}/attention").status_code == 403
    assert (
        client.get(f"{BASE}/attention", headers={"X-Operator-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.get(f"{BASE}/attention", headers={"X-Operator-Token": "s3cret"}).status_code
        == 200
    )


def test_a_refused_request_uses_the_api_error_convention(client, monkeypatch):
    monkeypatch.setattr(settings, "OPERATOR_API_TOKEN", "s3cret")
    body = client.get(f"{BASE}/attention").json()

    assert body["code"] == "forbidden"
    assert isinstance(body["detail"], str)


def test_a_disabled_operator_api_is_invisible_rather_than_forbidden(
    client, monkeypatch
):
    """404, not 403: a switched-off admin surface should not confirm it exists."""
    monkeypatch.setattr(settings, "OPERATOR_API_ENABLED", False)

    response = client.get(f"{BASE}/attention")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_the_boundary_is_not_the_caller_identity(client, db, user, storage):
    """An operator sees other people's documents. That is the whole point, and
    it is why this surface has its own boundary rather than reusing CurrentUser."""
    document = upload(db, user, storage)
    fail(db, service.start_run(db, document=document), "malformed_pdf")

    # A completely different dev identity still sees it.
    body = client.get(
        f"{BASE}/attention", headers={"X-Dev-User-Email": "someone-else@example.com"}
    ).json()
    assert document.id in [d["document_id"] for d in body["documents"]]


# --- the six states -----------------------------------------------------------


def test_an_exhausted_document_is_distinguishable(client, db, user, storage, monkeypatch):
    """The state that needs a person: worth retrying, and out of budget."""
    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 2)
    document = upload(db, user, storage)

    first = fail(db, service.start_run(db, document=document), "ai_rate_limited")
    second = retry.schedule_retry(db, failed_run=first, queue=ExternalQueue())
    fail(db, second, "ai_rate_limited")

    body = client.get(f"{BASE}/attention").json()
    entry = next(d for d in body["documents"] if d["document_id"] == document.id)

    assert entry["state"] == operations.EXHAUSTED
    assert entry["attempts_used"] == 2
    assert entry["max_attempts"] == 2
    assert entry["failure_classification"] == "transient"
    assert entry["error_code"] == "ai_rate_limited"


def test_a_permanent_first_failure_is_not_called_exhausted(
    client, db, user, storage
):
    """Both are 'failed and not retrying'. Conflating them sends an operator to
    look for an outage when the answer is that the file cannot be read."""
    document = upload(db, user, storage, data=make_image_only_pdf(1), filename="scan.pdf")
    fail(db, service.start_run(db, document=document), "ocr_unavailable", stage="ocr")

    body = client.get(f"{BASE}/attention").json()
    entry = next(d for d in body["documents"] if d["document_id"] == document.id)

    assert entry["state"] == operations.FAILED_PERMANENT
    assert entry["attempts_used"] == 1
    assert entry["failure_classification"] == "permanent"


def test_a_retry_waiting_out_its_backoff_is_distinguishable(
    client, db, user, storage
):
    """Temporarily waiting is not a problem, and must not be listed as one."""
    document = upload(db, user, storage)
    first = fail(db, service.start_run(db, document=document), "ai_timeout")
    scheduled = retry.schedule_retry(db, failed_run=first, queue=ExternalQueue())
    assert scheduled.next_retry_at is not None

    body = client.get(f"{BASE}/attention?include_all=true").json()
    entry = next(d for d in body["documents"] if d["document_id"] == document.id)

    assert entry["state"] == operations.WAITING_RETRY
    assert entry["next_retry_at"] is not None
    # And it is NOT in the default, attention-only view.
    attention = client.get(f"{BASE}/attention").json()
    assert document.id not in [d["document_id"] for d in attention["documents"]]


def test_a_run_being_worked_on_is_distinguishable(client, db, user, storage):
    document = upload(db, user, storage)
    run = service.start_run(db, document=document)
    lease.acquire(db, run_id=run.id, owner="worker-a", lease_seconds=300)

    body = client.get(f"{BASE}/attention?include_all=true").json()
    entry = next(d for d in body["documents"] if d["document_id"] == document.id)

    assert entry["state"] == operations.PROCESSING
    assert entry["leased"] is True
    assert entry["heartbeat_at"] is not None


def test_a_completed_document_needs_no_attention(client, db, user, storage, queue):
    document = upload(db, user, storage)
    service.start_run(db, document=document, queue=queue)
    queue.drain()

    attention = client.get(f"{BASE}/attention").json()
    assert document.id not in [d["document_id"] for d in attention["documents"]]

    everything = client.get(f"{BASE}/attention?include_all=true").json()
    entry = next(d for d in everything["documents"] if d["document_id"] == document.id)
    assert entry["state"] == operations.COMPLETED


def test_the_counts_cover_every_state_not_just_the_filtered_ones(
    client, db, user, storage
):
    """One glance should distinguish a single bad document from an outage.

    The filter narrows the list, never the counts — an operator looking at one
    permanent failure needs to know whether forty others are waiting to retry.
    """
    healthy = upload(db, user, storage)
    service.start_run(db, document=healthy)

    broken = upload(db, user, storage, data=make_image_only_pdf(1), filename="s.pdf")
    fail(db, service.start_run(db, document=broken), "malformed_pdf", stage="extract")

    body = client.get(f"{BASE}/attention").json()

    # The list is filtered to what needs attention...
    assert [d["document_id"] for d in body["documents"]] == [broken.id]
    # ...while the counts still describe everything.
    assert body["counts"][operations.FAILED_PERMANENT] == 1
    assert body["counts"][operations.PROCESSING] == 1
    assert "generated_at" in body


# --- what must never be returned ----------------------------------------------


def test_no_resume_content_reaches_the_operator_api(client, db, user, storage, queue):
    """The surface reads across users, so the data minimisation is doing the
    work the authorization cannot do yet."""
    document = upload(db, user, storage, filename="Jane Okonkwo CV.pdf")
    service.start_run(db, document=document, queue=queue)
    queue.drain()

    bodies = [
        client.get(f"{BASE}/attention?include_all=true").text,
        client.get(f"{BASE}/documents/{document.id}").text,
    ]
    run_id = client.get(f"{BASE}/documents/{document.id}").json()["runs"][0]["id"]
    bodies.append(client.get(f"{BASE}/runs/{run_id}").text)

    for body in bodies:
        # The filename is routinely the candidate's own name.
        assert "Jane Okonkwo" not in body
        assert "original_filename" not in body
        # Where the bytes live, and the fingerprint that would identify them.
        assert "storage_key" not in body
        assert "content_sha256" not in body
        assert "var/resume-storage" not in body
        # The extracted text, and the structured reading that quotes it again.
        assert "payload" not in body
        assert "unmapped" not in body
        assert "provenance" not in body
        # The person.
        assert user.email not in body
        assert "email" not in body
        # Server-authored today, unconstrained forever.
        assert "error_message" not in body
        # Infrastructure identity.
        assert "lease_owner" not in body


def test_extracted_text_is_never_returned_even_though_it_was_read(
    client, db, user, storage, queue
):
    """The run genuinely has pages with verbatim text behind a relationship.
    Nothing in the operator response may reach them."""
    from app.models import ResumeDocumentPage

    document = upload(db, user, storage)
    service.start_run(db, document=document, queue=queue)
    queue.drain()

    pages = db.query(ResumeDocumentPage).all()
    assert pages and any(page.text for page in pages), "fixture should have text"
    sample = next(page.text for page in pages if page.text)[:40]

    run_id = client.get(f"{BASE}/documents/{document.id}").json()["runs"][0]["id"]
    assert sample not in client.get(f"{BASE}/runs/{run_id}").text


def test_provider_metadata_is_projected_to_an_allowlist():
    """The column is unvalidated JSONB written by adapters. A future adapter
    returning a richer debug object must not have it served verbatim."""
    projected = operations.safe_provider_metadata(
        {
            "latency_ms": 120,
            "input_tokens": 900,
            "stop_reason": "end_turn",
            "response_id": "msg_01_secret_handle",
            "raw_prompt": "JANE OKONKWO, 42 Acacia Avenue",
            "api_key": "sk-ant-xxx",
        }
    )
    assert projected == {
        "latency_ms": 120,
        "input_tokens": 900,
        "stop_reason": "end_turn",
    }
    assert operations.safe_provider_metadata(None) is None
    assert operations.safe_provider_metadata({"only_unknown": 1}) is None


# --- inspecting one document and one run --------------------------------------


def test_a_document_shows_every_attempt_in_order(client, db, user, storage, monkeypatch):
    """The history is the diagnosis: three failures on one code is a different
    problem from three on different ones."""
    monkeypatch.setattr(settings, "RESUME_MAX_ATTEMPTS", 3)
    document = upload(db, user, storage)

    first = fail(db, service.start_run(db, document=document), "ai_rate_limited")
    second = retry.schedule_retry(db, failed_run=first, queue=ExternalQueue())
    fail(db, second, "ai_timeout")

    body = client.get(f"{BASE}/documents/{document.id}").json()

    assert [r["attempt"] for r in body["runs"]] == [1, 2]
    assert [r["error_code"] for r in body["runs"]] == ["ai_rate_limited", "ai_timeout"]
    assert body["attempts_used"] == 2
    assert body["max_attempts"] == 3
    # Attempt 1 was not rewritten when attempt 2 opened.
    assert body["runs"][0]["status"] == "failed"


def test_a_run_shows_its_stage_timings(client, db, user, storage, queue):
    document = upload(db, user, storage)
    service.start_run(db, document=document, queue=queue)
    queue.drain()

    run_id = client.get(f"{BASE}/documents/{document.id}").json()["runs"][0]["id"]
    body = client.get(f"{BASE}/runs/{run_id}").json()

    stages = {event["stage"] for event in body["events"]}
    assert {"validate", "extract", "structure", "materialise"} <= stages
    assert body["structurer"] == "deterministic"
    assert body["schema_version"]


def test_an_unknown_document_or_run_is_a_404(client):
    assert client.get(f"{BASE}/documents/999999").status_code == 404
    assert client.get(f"{BASE}/runs/999999").status_code == 404


# --- the derivation itself ----------------------------------------------------


def test_every_state_is_derived_from_runs_with_no_stored_flag():
    """No operator status column exists, so nothing can drift out of step."""
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

    def run(**kwargs):
        made = ResumeProcessingRun(resume_document_id=1, attempt=kwargs.pop("attempt", 1))
        made.status = kwargs.pop("status", "uploaded")
        for key, value in kwargs.items():
            setattr(made, key, value)
        return made

    assert operations.derive_state(
        run(status="completed"), attempts_used=1, now=now
    ) == operations.COMPLETED

    assert operations.derive_state(
        run(status="failed", error_code="malformed_pdf"), attempts_used=1, now=now
    ) == operations.FAILED_PERMANENT

    assert operations.derive_state(
        run(status="failed", error_code="ai_timeout"),
        attempts_used=3,
        now=now,
        max_attempts=3,
    ) == operations.EXHAUSTED

    assert operations.derive_state(
        run(next_retry_at=now + timedelta(minutes=5)), attempts_used=2, now=now
    ) == operations.WAITING_RETRY

    assert operations.derive_state(
        run(lease_owner="w", leased_until=now + timedelta(minutes=1)),
        attempts_used=1,
        now=now,
    ) == operations.PROCESSING

    assert operations.derive_state(run(), attempts_used=1, now=now) == operations.PROCESSING

    # Started, unleased, due: a worker let go without finishing.
    assert operations.derive_state(
        run(started_at=now - timedelta(minutes=10)), attempts_used=1, now=now
    ) == operations.STALLED

    # A document with no run at all.
    assert operations.derive_state(None, attempts_used=0, now=now) == operations.STALLED


def test_an_unclassified_failure_reads_as_permanent_not_exhausted():
    """It is treated as permanent by the retry policy, so the operator view must
    agree — otherwise the two disagree about the same document."""
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    run = ResumeProcessingRun(resume_document_id=1, attempt=1)
    run.status = "failed"
    run.error_code = "something_nobody_classified"

    assert operations.derive_state(run, attempts_used=1, now=now) == operations.FAILED_PERMANENT


def test_needs_attention_is_exactly_the_three_actionable_states():
    assert operations.NEEDS_ATTENTION == frozenset(
        {operations.EXHAUSTED, operations.FAILED_PERMANENT, operations.STALLED}
    )
    for healthy in (operations.COMPLETED, operations.PROCESSING, operations.WAITING_RETRY):
        assert healthy not in operations.NEEDS_ATTENTION
