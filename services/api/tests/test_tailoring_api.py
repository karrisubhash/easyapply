"""The tailoring HTTP surface, including the one route that returns bytes.

Two things are being proved here that no other test file can prove:

  * The API returns BEFORE the work runs. The queue fixture records rather than
    executes, so "the run was created and queued" and "the run finished" are two
    observable events rather than one — which is the only way to check that a
    slow model does not become a slow request.

  * Ownership holds on every route, including the download. A tailored resume
    is somebody's entire career history rendered into a file, and the artifact
    route is the one place in this application where getting that wrong hands
    over the document itself rather than a summary of it.
"""

from __future__ import annotations

import pytest

from app.models.tailoring import GROUNDING_PASSED
from app.services.storage import InMemoryStorage
from app.services.tailoring import service
from tests.matching_fixtures import a_candidate, another_user, persisted_profile_version
from tests.tailoring_fixtures import a_backend_job, a_tailorable_version

STRANGER = {"X-Dev-User-Email": "stranger@example.com"}


@pytest.fixture
def storage(monkeypatch):
    """One in-memory backend for the whole request/worker round trip.

    Patched at both ends because the pipeline and the download read storage
    independently — a test using two different backends would pass the write
    and fail the read for reasons that have nothing to do with the code.
    """
    backend = InMemoryStorage()
    monkeypatch.setattr("app.services.tailoring.pipeline.get_storage", lambda: backend)
    monkeypatch.setattr("app.services.tailoring.service.get_storage", lambda: backend)
    return backend


@pytest.fixture
def caller(db):
    """The identity the dev header resolves to, with a profile and a job."""
    from app.models import User

    account = db.query(User).filter_by(email="dev@easyapply.local").one_or_none()
    if account is None:
        account = User(email="dev@easyapply.local")
        db.add(account)
        db.flush()
    version = a_tailorable_version(db, account)
    job, requirements = a_backend_job(db, account)
    return account, version, job


def complete_one(client, tailoring_queue, job_id: int) -> dict:
    """Start a run and let the worker finish it, as the UI's poll would."""
    started = client.post(f"/api/v1/jobs/{job_id}/tailored-resumes", json={})
    assert started.status_code == 201, started.text
    tailoring_queue.drain()
    return client.get(f"/api/v1/tailored-resumes/{started.json()['id']}").json()


# --- starting -------------------------------------------------------------------


def test_starting_a_run_answers_before_the_work_happens(client, tailoring_queue, caller, storage):
    _, _, job = caller

    response = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["result"] is None
    assert body["artifact"] is None
    assert tailoring_queue.enqueued == [body["id"]]


def test_the_run_names_the_profile_version_it_will_use(client, tailoring_queue, caller, storage):
    _, version, job = caller

    body = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()

    assert body["profile_version_id"] == version.id
    assert body["profile_version_number"] == version.version_number


def test_a_second_request_while_one_is_in_flight_returns_the_same_run(
    client, tailoring_queue, caller, storage
):
    """A double-click must not start a second generation."""
    _, _, job = caller

    first = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})
    second = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def _age(db, run_id: int, *, hours: float = 2.0, status: str | None = None) -> None:
    """Make a run look as old as a lost one, and optionally mid-stage."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.models.tailoring import TailoringRun

    where = TailoringRun.id == run_id
    db.execute(
        update(TailoringRun)
        .where(where)
        .values(created_at=datetime.now(timezone.utc) - timedelta(hours=hours))
    )
    # One legal step at a time: `trg_tailoring_runs_transition` refuses a jump.
    path = ("generating", "validating", "rendering")
    for step in path[: path.index(status) + 1] if status else ():
        db.execute(update(TailoringRun).where(where).values(status=step))
    db.commit()
    db.expire_all()


def test_a_stalled_run_nobody_is_working_on_does_not_block_generating_again(
    client, tailoring_queue, db, caller, storage
):
    """H1. A run whose queue was lost (an API restart drops the in-process pool)
    stays `queued` forever. It used to be returned to every later Generate as
    "already going", so that job and version could never be tailored again."""
    from app.models.tailoring import TailoringRun

    _, _, job = caller
    lost = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()
    _age(db, lost["id"])
    assert client.get(f"/api/v1/tailored-resumes/{lost['id']}").json()["is_stalled"] is True

    again = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert again.status_code == 201, again.text
    assert again.json()["id"] != lost["id"]
    assert again.json()["attempt"] == lost["attempt"] + 1
    assert tailoring_queue.enqueued[-1] == again.json()["id"]

    old = db.get(TailoringRun, lost["id"])
    db.refresh(old)
    assert old.status == "failed"
    assert old.error_code == "worker_abandoned"
    assert old.failed_stage == "generate"
    assert old.finished_at is not None
    assert "generating again is safe" in old.error_message
    assert service.can_retry(old) is True

    # And the new attempt is the one that runs, to a document.
    tailoring_queue.drain()
    finished = client.get(f"/api/v1/tailored-resumes/{again.json()['id']}").json()
    assert finished["status"] == "completed"


@pytest.mark.parametrize(
    "status,stage", [("generating", "generate"), ("validating", "validate"), ("rendering", "render")]
)
def test_an_abandoned_run_names_the_stage_it_was_in(
    client, tailoring_queue, db, caller, storage, status, stage
):
    from app.models.tailoring import TailoringRun

    _, _, job = caller
    lost = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()
    _age(db, lost["id"], status=status)

    assert client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).status_code == 201
    old = db.get(TailoringRun, lost["id"])
    db.refresh(old)
    assert (old.status, old.failed_stage, old.error_code) == ("failed", stage, "worker_abandoned")


def test_a_worker_reaching_an_abandoned_run_leaves_it_alone(
    client, tailoring_queue, db, caller, storage
):
    """The lost message is sometimes only late. When the old id finally reaches a
    worker, the run is terminal and nothing is generated for it."""
    from app.models.tailoring import TailoringRun

    _, _, job = caller
    lost = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()
    _age(db, lost["id"])
    client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    tailoring_queue.drain()

    old = db.get(TailoringRun, lost["id"])
    db.refresh(old)
    assert old.status == "failed"
    assert old.result is None


def _stale_first_read(monkeypatch, name: str, stale):
    """Make the service's first call to `name` return what a racing request
    would have seen before the other one committed, and every later call real."""
    real = getattr(service, name)
    calls = {"n": 0}

    def fake(*args, **kwargs):
        calls["n"] += 1
        return stale if calls["n"] == 1 else real(*args, **kwargs)

    monkeypatch.setattr(service, name, fake)


def test_two_starts_racing_for_one_attempt_number_resolve_to_the_same_run(
    client, tailoring_queue, caller, storage, monkeypatch
):
    """L7. Both requests found nothing in flight and both computed attempt 1.
    The loser's INSERT violated uq_(job, version, attempt) and answered 500."""
    _, _, job = caller
    first = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()

    _stale_first_read(monkeypatch, "_in_flight", None)
    _stale_first_read(monkeypatch, "_next_attempt", first["attempt"])
    second = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert second.status_code == 200, second.text
    assert second.json()["id"] == first["id"]
    assert tailoring_queue.enqueued == [first["id"]]


def test_losing_the_race_to_a_finished_run_takes_the_next_attempt(
    client, tailoring_queue, caller, storage, monkeypatch
):
    _, _, job = caller
    done = complete_one(client, tailoring_queue, job.id)

    _stale_first_read(monkeypatch, "_next_attempt", done["attempt"])
    again = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert again.status_code == 201, again.text
    assert again.json()["attempt"] == done["attempt"] + 1


def test_generating_again_after_one_finished_is_a_new_attempt(
    client, tailoring_queue, caller, storage
):
    """Unlike a match, a tailored resume is a draft worth regenerating."""
    _, _, job = caller

    first = complete_one(client, tailoring_queue, job.id)
    second = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]
    assert second.json()["attempt"] == first["attempt"] + 1


def test_a_draft_version_is_refused(client, tailoring_queue, db, caller, storage):
    account, _, job = caller
    draft = persisted_profile_version(
        db,
        account,
        version_number=9,
        confirmed=False,
        is_current=False,
        experiences=[{"description": "Wrote things."}],
    )

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes", json={"profile_version_id": draft.id}
    )

    assert response.status_code == 409
    assert "confirmed" in response.json()["detail"].lower()


def test_an_unstructured_job_is_refused(client, tailoring_queue, db, caller, storage):
    from app.models import Job

    account, _, _ = caller
    bare = Job(
        user_id=account.id,
        source_type="other",
        source_url="https://example.com/jobs/1",
        canonical_url="https://example.com/jobs/1",
        content_hash="b" * 64,
        job_title="Unstructured",
        raw_payload={},
    )
    db.add(bare)
    db.commit()

    response = client.post(f"/api/v1/jobs/{bare.id}/tailored-resumes", json={})

    assert response.status_code == 409
    assert "structured" in response.json()["detail"].lower()


def test_a_match_for_this_version_and_reading_of_the_job_is_accepted(
    client, tailoring_queue, caller, storage
):
    _, _, job = caller
    match = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes", json={"match_result_id": match["id"]}
    )

    assert response.status_code == 201, response.text
    assert response.json()["match_result_id"] == match["id"]


def test_a_match_made_for_a_different_profile_version_is_refused(
    client, tailoring_queue, db, caller, storage
):
    """L1. The match's verdicts steer what the document leads with and what a
    model may claim; verdicts about version 1 do not describe version 2."""
    account, first, job = caller
    match = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()
    assert match["profile_version_id"] == first.id
    second = a_tailorable_version(db, account, version_number=2)

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes",
        json={"profile_version_id": second.id, "match_result_id": match["id"]},
    )

    assert response.status_code == 409
    assert "different profile version" in response.json()["detail"]
    assert tailoring_queue.enqueued == []


def test_a_match_made_against_an_earlier_reading_of_the_job_is_refused(
    client, tailoring_queue, db, caller, storage
):
    """L1. Reprocessing a job writes a new requirements row; the run pins the
    newest, and a match against the old one judged a different list."""
    from tests.matching_fixtures import a_structured_job

    account, _, job = caller
    match = client.post(f"/api/v1/jobs/{job.id}/matches", json={}).json()
    a_structured_job(db, account, job=job, attempt=2)

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes", json={"match_result_id": match["id"]}
    )

    assert response.status_code == 409
    assert "earlier reading of this job" in response.json()["detail"]
    assert tailoring_queue.enqueued == []


def test_a_payload_naming_a_field_the_server_owns_is_rejected(client, caller, storage):
    _, _, job = caller

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes",
        json={"generator": "ai", "user_id": 1},
    )

    assert response.status_code == 422


# --- reading ---------------------------------------------------------------------


def test_a_completed_run_exposes_the_document_and_the_verdict(
    client, tailoring_queue, caller, storage
):
    _, _, job = caller

    body = complete_one(client, tailoring_queue, job.id)

    assert body["status"] == "completed"
    assert body["grounding_status"] == GROUNDING_PASSED
    assert body["result"]["is_approved"] is True
    assert body["result"]["document"]["experience"]
    assert body["artifact"]["filename"].endswith(".docx")
    assert body["has_artifact"] is True


def test_the_response_never_says_where_the_file_is(client, tailoring_queue, caller, storage):
    """A client that cannot name a storage location cannot ask for one."""
    _, _, job = caller

    body = complete_one(client, tailoring_queue, job.id)

    assert "storage_key" not in str(body)
    assert "tailored/" not in str(body)


def test_a_run_shows_the_original_text_behind_every_generated_line(
    client, tailoring_queue, caller, storage
):
    """What the review screen's ORIGINAL -> TAILORED display is built from."""
    _, _, job = caller

    body = complete_one(client, tailoring_queue, job.id)
    bullets = [
        bullet
        for entry in body["result"]["document"]["experience"]
        for bullet in entry["bullets"]
    ]

    assert bullets
    assert all(bullet["source_quote"] for bullet in bullets)
    assert all(bullet["source_field"] for bullet in bullets)


def test_listing_shows_this_caller_s_runs_newest_first(client, tailoring_queue, caller, storage):
    _, _, job = caller
    complete_one(client, tailoring_queue, job.id)
    client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={})

    listed = client.get("/api/v1/tailored-resumes").json()

    assert len(listed) == 2
    assert listed[0]["attempt"] > listed[1]["attempt"]


def test_listing_for_a_job_is_scoped_to_that_job(client, tailoring_queue, db, caller, storage):
    account, _, job = caller
    other_job, _ = a_backend_job(db, account, external_job_id="tailor-2")
    complete_one(client, tailoring_queue, job.id)

    for_other = client.get(f"/api/v1/jobs/{other_job.id}/tailored-resumes").json()

    assert for_other == []


# --- the artifact --------------------------------------------------------------------


def test_the_artifact_downloads_with_a_filename_and_the_right_type(
    client, tailoring_queue, caller, storage
):
    _, _, job = caller
    body = complete_one(client, tailoring_queue, job.id)

    response = client.get(f"/api/v1/tailored-resumes/{body['id']}/artifact")

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in response.headers["content-disposition"]
    assert ".docx" in response.headers["content-disposition"]
    assert response.content.startswith(b"PK")


def test_the_artifact_is_not_cached_by_anything_in_front(client, tailoring_queue, caller, storage):
    _, _, job = caller
    body = complete_one(client, tailoring_queue, job.id)

    response = client.get(f"/api/v1/tailored-resumes/{body['id']}/artifact")

    assert "no-store" in response.headers["cache-control"]


def test_a_run_still_generating_has_nothing_to_download(client, tailoring_queue, caller, storage):
    _, _, job = caller
    started = client.post(f"/api/v1/jobs/{job.id}/tailored-resumes", json={}).json()

    response = client.get(f"/api/v1/tailored-resumes/{started['id']}/artifact")

    assert response.status_code == 409
    assert "finished" in response.json()["detail"].lower()


def test_a_document_that_failed_grounding_cannot_be_downloaded(
    client, tailoring_queue, db, caller, storage, monkeypatch
):
    """§9's requirement, end to end through HTTP.

    The run completes, the document is readable, the verdict is visible — and
    the file is refused. All four at once, because any three of them without the
    fourth is a worse product than none of them.
    """
    from app.schemas.tailored_resume import (
        TailoredExperience,
        TailoredLine,
        TailoredResumeDocument,
    )
    from app.services.tailoring import pipeline
    from app.services.tailoring.generation import GenerationResult

    _, version, job = caller
    experience = version.experiences[0]

    class InventingTailor:
        def generate(self, inputs):
            return GenerationResult(
                document=TailoredResumeDocument(
                    experience=[
                        TailoredExperience(
                            source_id=experience.id,
                            company=experience.company,
                            job_title=experience.job_title,
                            location=experience.location,
                            start_date=experience.start_date,
                            start_date_precision=experience.start_date_precision,
                            end_date=experience.end_date,
                            end_date_precision=experience.end_date_precision,
                            bullets=[
                                TailoredLine(
                                    text="Ran the team's on-call rotation for 40 engineers.",
                                    source_quote="Ran the team's on-call rotation.",
                                    source_field="description",
                                )
                            ],
                        )
                    ]
                ),
                generator="ai",
                model="test-model",
                prompt_version="test",
            )

    monkeypatch.setattr(pipeline, "get_tailor", lambda _generator=None: InventingTailor())

    body = complete_one(client, tailoring_queue, job.id)

    assert body["status"] == "completed"
    assert body["grounding_status"] == "failed"
    assert body["result"]["is_approved"] is False
    assert body["artifact"] is None
    assert body["has_artifact"] is False
    assert any(issue["severity"] == "error" for issue in body["result"]["issues"])

    refused = client.get(f"/api/v1/tailored-resumes/{body['id']}/artifact")
    assert refused.status_code == 409
    assert "not approved" in refused.json()["detail"].lower()


def test_a_rejected_document_says_what_was_wrong_with_it(
    client, tailoring_queue, caller, storage, monkeypatch
):
    """A refusal the user cannot act on is a dead end."""
    from app.schemas.tailored_resume import (
        TailoredExperience,
        TailoredLine,
        TailoredResumeDocument,
    )
    from app.services.tailoring import pipeline
    from app.services.tailoring.generation import GenerationResult

    _, version, job = caller
    experience = version.experiences[0]

    class InventingTailor:
        def generate(self, inputs):
            return GenerationResult(
                document=TailoredResumeDocument(
                    experience=[
                        TailoredExperience(
                            source_id=experience.id,
                            company=experience.company,
                            job_title=experience.job_title,
                            location=experience.location,
                            start_date=experience.start_date,
                            start_date_precision=experience.start_date_precision,
                            end_date=experience.end_date,
                            end_date_precision=experience.end_date_precision,
                            bullets=[
                                TailoredLine(
                                    text="Ran the team's on-call rotation for 40 engineers.",
                                    source_quote="Ran the team's on-call rotation.",
                                    source_field="description",
                                )
                            ],
                        )
                    ]
                ),
                generator="ai",
                model="test-model",
                prompt_version="test",
            )

    monkeypatch.setattr(pipeline, "get_tailor", lambda _generator=None: InventingTailor())

    body = complete_one(client, tailoring_queue, job.id)
    issue = next(i for i in body["result"]["issues"] if i["severity"] == "error")

    assert issue["section"] == "experience"
    assert issue["field_path"].startswith("experience[0].bullets")
    assert "40 engineers" in issue["claim"]
    assert issue["reason"]


# --- ownership ------------------------------------------------------------------------


def test_another_user_cannot_read_the_run(client, tailoring_queue, db, caller, storage):
    _, _, job = caller
    another_user(db)
    body = complete_one(client, tailoring_queue, job.id)

    response = client.get(f"/api/v1/tailored-resumes/{body['id']}", headers=STRANGER)

    assert response.status_code == 404


def test_another_user_cannot_download_the_artifact(client, tailoring_queue, db, caller, storage):
    """The worst failure this application could have, asserted directly."""
    _, _, job = caller
    another_user(db)
    body = complete_one(client, tailoring_queue, job.id)

    response = client.get(f"/api/v1/tailored-resumes/{body['id']}/artifact", headers=STRANGER)

    assert response.status_code == 404
    assert b"PK" not in response.content


def test_another_user_cannot_tailor_against_someone_else_s_job(
    client, tailoring_queue, db, caller, storage
):
    _, _, job = caller
    another_user(db)

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes", json={}, headers=STRANGER
    )

    assert response.status_code == 404


def test_another_user_s_profile_version_cannot_be_named(
    client, tailoring_queue, db, caller, storage
):
    """The insecure-direct-object-reference, tried on purpose."""
    stranger = another_user(db)
    stranger_version = persisted_profile_version(
        db, stranger, version_number=1, experiences=[{"description": "Their career."}]
    )
    _, _, job = caller

    response = client.post(
        f"/api/v1/jobs/{job.id}/tailored-resumes",
        json={"profile_version_id": stranger_version.id},
    )

    assert response.status_code == 404


def test_another_user_s_run_is_absent_from_the_listing(client, tailoring_queue, db, caller, storage):
    _, _, job = caller
    another_user(db)
    complete_one(client, tailoring_queue, job.id)

    listed = client.get("/api/v1/tailored-resumes", headers=STRANGER).json()

    assert listed == []


def test_an_id_that_does_not_exist_is_a_not_found(client, caller, storage):
    assert client.get("/api/v1/tailored-resumes/999999").status_code == 404
    assert client.get("/api/v1/tailored-resumes/999999/artifact").status_code == 404
