"""The whole product, once, through the real boundaries.

Every other test in this suite proves one thing well. This one proves that the
things fit together — that a person who starts with a PDF and a job posting ends
up with a Word document they can send, and that nothing along the way quietly
lies to them.

It is deliberately ONE long test rather than fifteen small ones. The failure it
exists to catch is an integration failure: two modules that each pass their own
tests and disagree about a field name, a status value, or who owns a row.
Splitting it into steps that each rebuild the world would test the fixtures
instead of the seam.

WHAT IS REAL HERE. Everything except the clock and the browser:

  * A real PostgreSQL database, built by the real Alembic migrations.
  * A real PDF, parsed by the real extractor.
  * The real processing pipeline, on a real worker, reached through the real
    queue — the API returns before any of it has happened, and this test drains
    the queue explicitly so that "the request answered" and "the work ran" stay
    two separate observable events.
  * The real job ingestion, normalization and structuring.
  * The real matching engine, over the real profile snapshot.
  * The real tailoring generator, the real grounding validator, the real DOCX
    renderer and the real storage backend.
  * Real HTTP, through the real routers, with the real identity dependency.

Nothing is monkeypatched except the storage ROOT, which points at a temporary
directory so the test does not write into the developer's own var/.

WHAT IS NOT COVERED, and is stated here rather than implied: the browser. This
exercises every boundary the web app calls, but it does not drive the web app.
The frontend's own suite covers the components against a mocked API, and the
generated OpenAPI contract is what keeps the two halves honest about shapes.
A Playwright pass over the running stack is the thing that would close that gap.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from app.core.config import settings
from tests.job_fixtures import make_job_payload
from tests.resume_fixtures import make_resume_pdf

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

STRANGER = {"X-Dev-User-Email": "stranger@example.com"}

#: What the user writes into the draft while reviewing what the resume said.
#:
#: This is a real step of the product, not a shortcut around one: the flow is
#: upload -> review -> EDIT -> confirm, and the draft is editable precisely
#: because an extractor's reading of a PDF is a proposal. The prose matters for
#: what comes later — tailoring selects clauses out of these descriptions, so a
#: snapshot with no prose would give the generator nothing to select and would
#: make the rest of this test vacuous.
REVIEWED_EXPERIENCE = (
    "Built internal tooling in Python and FastAPI for the operations team. "
    "Migrated the reporting database to PostgreSQL. "
    "Ran the weekly on-call rotation. "
    "Organised the annual office relocation."
)


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    """Both storage roots point at a throwaway directory.

    The resume upload and the tailored artifact use the same backend with
    different key prefixes, so one root covers both.
    """
    monkeypatch.setattr(settings, "RESUME_STORAGE_ROOT", str(tmp_path))


def test_from_a_pdf_and_a_posting_to_a_downloadable_tailored_resume(
    client, queue, tailoring_queue
):
    # --- 1. the user uploads a resume ------------------------------------------
    #
    # The request must answer before the document has been read. A product that
    # blocks an upload on a model call is a product that times out.

    upload = client.post(
        "/api/v1/resumes",
        files={"file": ("resume.pdf", make_resume_pdf(), PDF_TYPE)},
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]
    assert upload.json()["latest_run"]["status"] == "uploaded"
    assert queue.enqueued == [upload.json()["latest_run"]["id"]]

    # Nothing about where the bytes live is ever on the wire.
    assert "storage_key" not in upload.text
    assert "resumes/" not in upload.text

    # --- 2. the worker reads it -------------------------------------------------

    queue.drain()

    result = client.get(f"/api/v1/resumes/{document_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["run"]["status"] == "completed"
    assert body["run"]["page_count"] == 2

    # The extractor genuinely read the document rather than producing an empty
    # shell — and every claim it made cites a page that really contains it.
    candidate = body["result"]["candidate"]
    assert candidate["skills"], "the resume lists skills; the reading found none"
    assert all(
        citation["verified"] is True
        for item in candidate["skills"]
        for citation in item["provenance"]
    )

    version_number = body["result"]["profile_version_number"]
    assert version_number is not None

    # --- 3. the user reviews the draft and corrects it ---------------------------

    draft = client.get(f"/api/v1/profile/versions/{version_number}")
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"
    assert draft.json()["is_current"] is False, "a draft is never authoritative"

    snapshot = draft.json()["snapshot"]
    snapshot["experiences"] = [
        {
            "company": "APTRANSCO",
            "job_title": "AI Developer Intern",
            "location": "Vijayawada, India",
            "start_date": "2024-05-01",
            "start_date_precision": "month",
            "end_date": "2024-08-01",
            "end_date_precision": "month",
            "description": REVIEWED_EXPERIENCE,
        }
    ]
    snapshot["skills"] = [
        {"name": "Python"},
        {"name": "FastAPI"},
        {"name": "PostgreSQL"},
        {"name": "Public speaking"},
    ]
    snapshot["education"] = [
        {
            "institution": "Anil Neerukonda Institute of Technology and Sciences",
            "degree": "B.Tech",
            "field_of_study": "Mechanical Engineering",
        }
    ]

    saved = client.put(
        f"/api/v1/profile/versions/{version_number}/snapshot", json=snapshot
    )
    assert saved.status_code == 200, saved.text

    # --- 4. and confirms it ------------------------------------------------------

    confirmed = client.post(
        f"/api/v1/profile/versions/{version_number}/confirm", json={}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["is_current"] is True
    profile_version_id = confirmed.json()["id"]

    # Confirmed means frozen. Everything generated below points back at this
    # exact snapshot, so it must not be able to move underneath them.
    refused = client.put(
        f"/api/v1/profile/versions/{version_number}/snapshot", json=snapshot
    )
    assert refused.status_code == 409

    # --- 5. the user saves a job posting ------------------------------------------

    posting = client.post(
        "/api/v1/jobs",
        json=make_job_payload(
            job_title="Backend Engineer",
            company_name="Acme Corp",
            external_job_id="e2e-1",
            description_text=(
                "Requirements:\n"
                "- Strong proficiency in Python\n"
                "- Experience with PostgreSQL\n"
                "- Bachelor's degree in Computer Science or related field\n"
                "\n"
                "Preferred:\n"
                "- Experience with Kubernetes\n"
                "\n"
                "Responsibilities:\n"
                "- Build and operate internal tooling\n"
            ),
        ),
    )
    assert posting.status_code == 201, posting.text
    job_id = posting.json()["id"]
    assert posting.json()["latest_run"]["status"] == "completed"

    requirements = client.get(f"/api/v1/jobs/{job_id}/requirements")
    assert requirements.status_code == 200
    structured = requirements.json()["requirements"]["candidate"]

    # A named technology lands in `technologies` with an is_required flag, not
    # in `required_skills` — the JobCandidate schema splits only skills and
    # experience into separate required/preferred lists, because "Kubernetes
    # required" and "Kubernetes preferred" are one technologies section making
    # two claims. See app/schemas/job_candidate.py.
    assert structured["technologies"], "the posting names technologies; none were read"
    assert structured["education"], "the posting states a degree requirement"

    # Required and preferred stay distinguishable, which is the whole reason the
    # structured reading is worth having.
    demanded = {
        item["name"].casefold(): item["is_required"] for item in structured["technologies"]
    }
    assert demanded.get("python") is True
    assert demanded.get("postgresql") is True
    assert demanded.get("kubernetes") is False

    # --- 6. the user matches their profile against it --------------------------------

    match = client.post(f"/api/v1/jobs/{job_id}/matches", json={})
    assert match.status_code == 201, match.text
    match_body = match.json()
    match_id = match_body["id"]

    assert match_body["profile_version_id"] == profile_version_id
    assert match_body["required_total"] > 0
    assert match_body["requirement_results"], "a match with no verdicts explains nothing"

    # The verdict the user came for, with the evidence that produced it.
    python_verdict = next(
        r for r in match_body["requirement_results"] if r["requirement_key"] == "python"
    )
    assert python_verdict["status"] == "matched"
    assert python_verdict["evidence"], "a MATCHED verdict must point at something"
    assert python_verdict["explanation"]

    # Coverage is a fraction of requirements, and the API says so in its own
    # field names. Nothing here is, or may be presented as, a probability.
    assert 0.0 <= match_body["required_coverage"] <= 1.0
    assert "probability" not in match.text.lower()

    # --- 7. the user tailors their resume for the job ----------------------------------
    #
    # Started from the match, which decides what the document LEADS with and
    # nothing else — see tailoring/pipeline.py::_emphasis.

    started = client.post(
        f"/api/v1/jobs/{job_id}/tailored-resumes",
        json={"match_result_id": match_id},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]

    # Again: the request answers before the work runs.
    assert started.json()["status"] == "queued"
    assert started.json()["result"] is None
    assert started.json()["artifact"] is None
    assert tailoring_queue.enqueued == [run_id]

    # --- 8. the worker generates, validates and renders it -------------------------------

    tailoring_queue.drain()

    tailored = client.get(f"/api/v1/tailored-resumes/{run_id}")
    assert tailored.status_code == 200
    run = tailored.json()

    assert run["status"] == "completed", run.get("error_message")
    assert run["grounding_status"] == "passed"
    assert run["error_count"] == 0
    assert run["result"]["is_approved"] is True

    # It says which snapshot it was built from, and it is the one just confirmed.
    assert run["profile_version_id"] == profile_version_id
    assert run["profile_version_number"] == version_number
    assert run["match_result_id"] == match_id

    # --- 9. the document is made of the user's own career, aimed at this job ---------------

    document = run["result"]["document"]
    assert document["experience"], "a resume with no experience is not a resume"

    entry = document["experience"][0]
    assert entry["company"] == "APTRANSCO"
    assert entry["job_title"] == "AI Developer Intern"
    assert entry["bullets"]

    # Tailoring's actual product is ORDER. The posting wants Python and
    # PostgreSQL; the office relocation is true, irrelevant, and must not lead.
    assert "Python" in entry["bullets"][0]["text"]
    assert "office relocation" not in entry["bullets"][0]["text"]

    # Every line carries the profile text it came from, which is what the review
    # screen shows as ORIGINAL -> TAILORED and what grounding checked.
    for bullet in entry["bullets"]:
        assert bullet["source_quote"]
        assert bullet["source_quote"] in REVIEWED_EXPERIENCE

    # The default generator SELECTS rather than writes, so nothing was reworded.
    assert all(bullet["text"] == bullet["source_quote"] for bullet in entry["bullets"])

    # It only claims skills the profile lists.
    claimed = {
        name for group in document["skills"] for name in group["skills"]
    }
    assert claimed <= {"Python", "FastAPI", "PostgreSQL", "Public speaking"}
    assert "Kubernetes" not in claimed, "the posting wants it; the profile does not have it"

    # --- 10. and the user downloads it --------------------------------------------------

    artifact = run["artifact"]
    assert artifact["media_type"] == DOCX_TYPE
    assert artifact["filename"].endswith(".docx")
    assert artifact["byte_size"] > 0
    assert "storage_key" not in tailored.text

    download = client.get(f"/api/v1/tailored-resumes/{run_id}/artifact")
    assert download.status_code == 200
    assert download.headers["content-type"] == DOCX_TYPE
    assert artifact["filename"] in download.headers["content-disposition"]
    assert "no-store" in download.headers["cache-control"]

    # A real Word document, containing the user's real career. Opened with the
    # library a recruiter's tooling would use, not sniffed for a magic number:
    # "it starts with PK" is also true of a corrupt zip.
    assert download.content.startswith(b"PK")
    text = "\n".join(
        paragraph.text for paragraph in Document(BytesIO(download.content)).paragraphs
    )
    assert "APTRANSCO" in text
    assert "AI Developer Intern" in text
    assert "Built internal tooling in Python and FastAPI for the operations team." in text

    # The document says whose career it is, and how to reply to it. This used to
    # assert the opposite thing — that the file named the ROLE it was written
    # for — which passed because the renderer printed "Prepared for Backend
    # Engineer" at the top, and passed on a document carrying no name, no email
    # and no phone number. A resume an employer cannot answer is not a resume,
    # and the posting's own title has no business on a page sent to the employer
    # who wrote it.
    assert "SUBHASH KUMAR" in text, "the file should say whose resume it is"
    assert "subhash@example.com" in text, "the file should say how to reply to it"
    assert "Prepared for" not in text
    assert "Generated by EasyApply" not in text

    # --- 11. and none of it belongs to anyone else ----------------------------------------
    #
    # Walked at the end rather than in a separate test, because the ids are only
    # real here. Every one of these is a resource a changed URL could otherwise
    # reach, and the last one is the user's entire career as a file.

    for path in (
        f"/api/v1/resumes/{document_id}",
        f"/api/v1/resumes/{document_id}/result",
        f"/api/v1/jobs/{job_id}",
        f"/api/v1/jobs/{job_id}/requirements",
        f"/api/v1/matches/{match_id}",
        f"/api/v1/tailored-resumes/{run_id}",
        f"/api/v1/tailored-resumes/{run_id}/artifact",
    ):
        denied = client.get(path, headers=STRANGER)
        assert denied.status_code == 404, f"{path} leaked to another user"
        assert b"PK" not in denied.content
        assert "APTRANSCO" not in denied.text


def test_a_generated_resume_that_cannot_be_grounded_is_never_downloadable(
    client, queue, tailoring_queue, monkeypatch
):
    """The same journey, with a generator that invents a number.

    Second only to the happy path in importance, and it shares its boundaries
    for a reason: the guarantee is not "grounding rejects a bad document in a
    unit test", it is "a bad document cannot reach the user through the
    product". So this walks the same route and stops where it should.
    """
    # A confirmed version and a structured job, by the same route as above but
    # without re-asserting the steps that test already covers.
    uploaded = client.post(
        "/api/v1/resumes", files={"file": ("cv.pdf", make_resume_pdf(), PDF_TYPE)}
    )
    queue.drain()

    document_id = uploaded.json()["id"]
    version_number = (
        client.get(f"/api/v1/resumes/{document_id}/result")
        .json()["result"]["profile_version_number"]
    )
    snapshot = client.get(f"/api/v1/profile/versions/{version_number}").json()["snapshot"]
    snapshot["experiences"] = [
        {
            "company": "APTRANSCO",
            "job_title": "AI Developer Intern",
            "start_date": "2024-05-01",
            "start_date_precision": "month",
            "end_date": "2024-08-01",
            "end_date_precision": "month",
            "description": "Ran the weekly on-call rotation.",
        }
    ]
    client.put(f"/api/v1/profile/versions/{version_number}/snapshot", json=snapshot)
    client.post(f"/api/v1/profile/versions/{version_number}/confirm", json={})

    job_id = client.post(
        "/api/v1/jobs", json=make_job_payload(external_job_id="e2e-2")
    ).json()["id"]

    # A generator that does what a resume writer is most tempted to do: put a
    # number on an achievement that never had one.
    from app.schemas.tailored_resume import (
        TailoredExperience,
        TailoredLine,
        TailoredResumeDocument,
    )
    from app.services.tailoring import pipeline
    from app.services.tailoring.generation import GenerationResult

    class InventingTailor:
        def generate(self, inputs):
            experience = inputs.version.experiences[0]
            return GenerationResult(
                document=TailoredResumeDocument(
                    experience=[
                        TailoredExperience(
                            source_id=experience.id,
                            company=experience.company,
                            job_title=experience.job_title,
                            start_date=experience.start_date,
                            start_date_precision=experience.start_date_precision,
                            end_date=experience.end_date,
                            end_date_precision=experience.end_date_precision,
                            bullets=[
                                TailoredLine(
                                    text="Ran the weekly on-call rotation for 40 engineers.",
                                    source_quote="Ran the weekly on-call rotation.",
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

    run_id = client.post(
        f"/api/v1/jobs/{job_id}/tailored-resumes", json={}
    ).json()["id"]
    tailoring_queue.drain()

    run = client.get(f"/api/v1/tailored-resumes/{run_id}").json()

    # The pipeline worked. The document is not a processing failure — it is a
    # finished answer carrying a verdict, and the two are different states the
    # UI has to be able to tell apart.
    assert run["status"] == "completed"
    assert run["failed_stage"] is None
    assert run["error_code"] is None

    # And the verdict is no.
    assert run["grounding_status"] == "failed"
    assert run["result"]["is_approved"] is False
    assert run["artifact"] is None
    assert run["has_artifact"] is False

    # The document is still returned, so the user can see what was written and
    # exactly which sentence was the problem.
    assert run["result"]["document"]["experience"][0]["bullets"][0]["text"] == (
        "Ran the weekly on-call rotation for 40 engineers."
    )
    # Found by the line it is about, not by position: the document is refused
    # for more than one reason (it also leaves out the contact block the
    # profile has), and which error sorts first is not the guarantee.
    error = next(
        issue
        for issue in run["result"]["issues"]
        if issue["severity"] == "error"
        and issue["field_path"] == "experience[0].bullets[0]"
    )
    assert "40 engineers" in error["claim"]
    assert error["reason"]

    # No file exists, and asking for one says so rather than 500ing or handing
    # over a document nobody vouched for.
    refused = client.get(f"/api/v1/tailored-resumes/{run_id}/artifact")
    assert refused.status_code == 409
    assert "not approved" in refused.json()["detail"].lower()
    assert b"PK" not in refused.content
