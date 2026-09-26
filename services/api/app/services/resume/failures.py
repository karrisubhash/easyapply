"""Which failures are worth trying again.

A retry is only ever worth making when the same inputs could plausibly produce a
different outcome. That splits every failure this domain can produce into two
kinds, and the split is explicit here rather than implied by a `try` somewhere:

  * TRANSIENT — the document is fine and something around it was not. A storage
    read that failed, a provider that was unreachable or rate limited, a worker
    that was killed mid-run. Running it again is the whole fix.

  * PERMANENT — the document, or our reading of it, is the problem. A DOCX that
    is not a DOCX, a password-protected PDF, a scan on a machine with no OCR
    engine. The same bytes through the same code produce the same failure, so a
    retry spends a worker to reach the same conclusion and delays telling the
    user the truth.

An unrecognised code is treated as PERMANENT. That is the deliberate direction
to be wrong in: a new failure mode we have not classified retried three times is
three times the damage and no new information, whereas one that is not retried
is visible, has a code, and can be reclassified in one line here.

The AI-provider half of this vocabulary (`ai_timeout`, `ai_output_invalid`, and
so on) is not decided here — it is decided once, in
`app/services/ai/failures.py`, and shared with `app/services/job/failures.py`.
The two domains must not be free to quietly disagree about whether a rate limit
is worth retrying.
"""

from app.services.ai.failures import AI_PERMANENT_CODES, AI_TRANSIENT_CODES

#: Storage and worker-level faults: nothing about the document caused them.
_TRANSIENT_INFRASTRUCTURE = frozenset(
    {
        # The object could not be read back. A blob store having a bad minute is
        # far more likely than the key being wrong, and a wrong key fails the
        # same way every time until attempts run out.
        "stored_object_missing",
        # The worker went away mid-run: killed, deployed over, or its lease
        # expired while it was stuck. Written by the reaper.
        "worker_abandoned",
        # A stage raised something we do not model. Usually a database or
        # network blip; bounded by the attempt limit if it is not.
        "unexpected_error",
        # The draft could not be written. Deadlocks and lost connections look
        # exactly like this.
        "draft_creation_failed",
    }
)

TRANSIENT_ERROR_CODES = _TRANSIENT_INFRASTRUCTURE | AI_TRANSIENT_CODES

#: Named rather than merely "everything else", so that adding a failure code
#: without classifying it is visible in review.
PERMANENT_ERROR_CODES = frozenset(
    {
        # --- the upload is not something we can read ---
        "empty_file",
        "file_too_large",
        "unsupported_file_type",
        "extension_mismatch",
        "media_type_mismatch",
        "missing_filename",
        "filename_too_long",
        "unsafe_filename",
        # --- the document cannot be extracted ---
        "malformed_pdf",
        "malformed_docx",
        "document_encrypted",
        "empty_document",
        "unsupported_media_type",
        "no_text_extracted",
        # The document unpacks (a DOCX's declared ZIP contents, a PDF stream
        # past pypdf's decompression bound) to far more than a resume holds.
        # The bytes decide it, so the next attempt decides it the same way.
        "document_too_large",
        # OCR is a property of the deployment, not of this minute. Retrying
        # cannot install Tesseract.
        "ocr_unavailable",
        "rasterise_failed",
        "ocr_failed",
        # --- our reading does not fit the profile ---
        # Deterministic given the same candidate document; a retry re-derives
        # the same shape and fails identically.
        "result_schema_invalid",
        # Structuring raised something the adapter could not classify.
        "structuring_failed",
    }
    | AI_PERMANENT_CODES
)


#: Permanent to the MACHINE, not to a person: failures caused by this
#: deployment rather than by the document, which a person may retry by hand.
#:
#: "Permanent" above means "the same bytes through the same deployment fail
#: the same way", which is exactly why none of these is retried automatically.
#: But the deployment is not the same forever: an operator installs Tesseract,
#: rotates a key, tops up an account, ships a fix. Refusing a MANUAL retry for
#: these wedged the document for good — a re-upload of the same bytes resolves
#: to the same document, so "upload a corrected file" had no file to correct.
#:
#: Explicit rather than "every permanent code", because most permanent codes
#: really are the document's: a password-protected PDF does not open after a
#: deploy, and offering a button for it would be a lie.
DEPLOYMENT_ERROR_CODES = frozenset(
    {
        # --- configuration: fixed by an operator, not by the user ---
        # No OCR engine installed or enabled. Installing one is the whole fix.
        "ocr_unavailable",
        # No key, or no provider configured.
        "ai_not_configured",
        # A key that is wrong, revoked or expired. Rotating it is the fix.
        "ai_unauthorized",
        "ai_authentication_failed",
        # The provider account is out of credit.
        "ai_billing_blocked",
        # --- our own code: fixed by a deploy, not by a different file ---
        # The structurer raised something no adapter classified — a bug of ours
        # that this document happened to reach.
        "structuring_failed",
        # Our mapping could not fit our own candidate schema onto our own
        # snapshot schema. Deterministic until the code changes, then not.
        "result_schema_invalid",
        # We sent the provider a request it rejected as malformed.
        "ai_request_invalid",
        # The model's output did not fit our schema: a prompt or schema problem,
        # which a prompt or schema deploy changes.
        "ai_output_invalid",
    }
)


def is_retryable(error_code: str | None) -> bool:
    """Whether another attempt could plausibly succeed, with nobody acting.

    This is the AUTOMATIC policy. A person's retry is `may_retry_manually`.
    """
    return error_code in TRANSIENT_ERROR_CODES


def may_retry_manually(error_code: str | None) -> bool:
    """Whether a person asking for another attempt could be answered with one.

    Transient failures, and failures the deployment caused — a person may know
    the outage is over or the engine is now installed, and one click is not a
    runaway. Not a failure the document itself causes: that attempt could only
    fail the same way, whatever anyone has changed since.
    """
    return error_code in TRANSIENT_ERROR_CODES or error_code in DEPLOYMENT_ERROR_CODES


def classification(error_code: str | None) -> str:
    """The label used in events and structured logs. Never the message."""
    if error_code in TRANSIENT_ERROR_CODES:
        return "transient"
    if error_code in PERMANENT_ERROR_CODES:
        return "permanent"
    return "unclassified"
