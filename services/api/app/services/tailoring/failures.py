"""Which tailoring failures are worth trying again.

The mirror of `app/services/resume/failures.py` and `app/services/job/
failures.py`, sized to this domain: there is no file to be corrupt here, so the
failures are an AI provider having a bad call (classified once in
`app/services/ai/failures.py`, shared so the three domains cannot disagree), a
renderer that could not write a document, and storage.

  * TRANSIENT — the inputs are fine and something around them was not. A
    provider that was unreachable, a storage write that failed, an exception a
    stage did not anticipate.

  * PERMANENT — the inputs, or our handling of them, are the problem. A
    generator that produced something that is not the schema, an empty
    snapshot, a rendering failure that the same document will reproduce.

An unrecognised code is treated as PERMANENT, the same deliberate direction the
other two domains argue for.

Grounding failure is NOT in either list, and that is the important line in this
file. A document that fails grounding did not fail to be produced — the pipeline
worked exactly as designed and reached a verdict. It is a completed run with
`grounding_status = 'failed'`, and offering "try again" for it would be offering
to re-roll a dice until the validator looks away.
"""

from app.services.ai.failures import AI_PERMANENT_CODES, AI_TRANSIENT_CODES

#: Written by `service.start_tailoring` onto a run it found stalled and
#: unattended — no worker holding it — when the user asked for the same
#: document again. The same name the resume domain's reaper writes, for the
#: same fact: the worker went away (a process restart that lost the in-memory
#: queue, a crash mid-run), and the inputs were never the problem.
ABANDONED_ERROR_CODE = "worker_abandoned"

#: What the user reads on that run. Plain words, and it says the one thing the
#: user needs to know to act: nothing was lost by starting again.
ABANDONED_ERROR_MESSAGE = (
    "This attempt stopped responding and was abandoned. "
    "Nothing was saved, so generating again is safe."
)

_TRANSIENT_INFRASTRUCTURE = frozenset(
    {
        # The worker went away: an API restart lost the in-process queue, or a
        # worker died mid-run. Written by `service.start_tailoring`.
        ABANDONED_ERROR_CODE,
        # The artifact could not be written. A blob store having a bad minute.
        "artifact_storage_failed",
        # A stage raised something we do not model. Usually a database or
        # network blip.
        "unexpected_error",
    }
)

TRANSIENT_ERROR_CODES = _TRANSIENT_INFRASTRUCTURE | AI_TRANSIENT_CODES

#: Named rather than merely "everything else", so that adding a failure code
#: without classifying it is visible in review.
PERMANENT_ERROR_CODES = (
    frozenset(
        {
            # The generator returned something that is not the document schema.
            # Deterministic given the same snapshot; the AI equivalent is
            # ai_output_invalid and is classified with the rest of that
            # vocabulary.
            "document_schema_invalid",
            # There is nothing to tailor: a confirmed version with no
            # experience, no projects, no education and no certifications. A
            # retry re-reads the same empty snapshot.
            "empty_profile_version",
            # The renderer could not turn a valid document into a file. The same
            # document will fail the same way.
            "render_failed",
            # A generator raised something its own adapter could not classify.
            "generation_failed",
        }
    )
    | AI_PERMANENT_CODES
)


def is_retryable(error_code: str | None) -> bool:
    """Whether another attempt could plausibly succeed."""
    return error_code in TRANSIENT_ERROR_CODES


def classification(error_code: str | None) -> str:
    """The label used in events and structured logs. Never the message."""
    if error_code in TRANSIENT_ERROR_CODES:
        return "transient"
    if error_code in PERMANENT_ERROR_CODES:
        return "permanent"
    return "unclassified"
