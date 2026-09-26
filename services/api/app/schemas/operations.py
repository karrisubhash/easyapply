"""What an operator endpoint is allowed to say.

Every model here is built field by field, and none of them sets
`from_attributes`. That is deliberate and load-bearing.

These endpoints read across ALL users — the only place in the application that
does. The ORM rows they read from reach, by relationship, to
`ResumeDocumentPage.text` (verbatim resume text), `ResumeStructuredResult.payload`
(a second verbatim copy, inside every provenance quote) and `.unmapped` (free
text describing what could not be mapped). `ResumeDocument` holds
`original_filename` — routinely the candidate's own name — plus `storage_key`
and `content_sha256`. `User` holds an email address.

With `from_attributes=True`, a field named `pages`, `payload` or `filename`
added here later would lazy-load and serialise any of that, with no query
anywhere saying so. Constructing each response explicitly means a leak has to be
typed out on purpose rather than arrived at by naming a field the same as a
column.

So the rule for this module: ids, timestamps, counts, and codes from a closed
vocabulary. Nothing derived from the document's content, and nothing that
identifies the person other than the user id an operator needs to correlate
support requests.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProcessingRunSummary(BaseModel):
    """One attempt, as an operator sees it.

    Absent on purpose:

      * `error_message` — server-authored today, but it is an unconstrained Text
        column with no CHECK and no test holding that line. One future
        `ProcessingFailed(stage, code, str(exc))` would put a parser library's
        exception text — which quotes document content and local paths — into a
        cross-user feed. `failed_stage` and `error_code` are a closed vocabulary
        and say the same thing about what went wrong.
      * the lease owner — a hostname and pid. Useful to an operator, but it
        names infrastructure, and `leased` plus `heartbeat_at` answers the
        actual question ("is anyone on this, and when were they last alive?").
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    document_id: int
    attempt: int
    max_attempts: int
    status: str
    failed_stage: str | None = None
    error_code: str | None = None
    #: transient / permanent / unclassified — why the retry policy did what it did.
    failure_classification: str | None = None

    page_count: int | None = None
    extracted_char_count: int | None = None
    ocr_page_count: int | None = None
    profile_version_id: int | None = None

    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None
    heartbeat_at: datetime | None = None
    leased: bool = False

    #: Which structurer produced the reading, and through which provider. Never
    #: the reading itself.
    structurer: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    #: Projected through operations.SAFE_PROVIDER_METADATA_KEYS — token counts,
    #: latency, stop reason. Never a prompt, a completion, a credential, or the
    #: vendor response id.
    provider_metadata: dict[str, Any] | None = None


class DocumentProcessingSummary(BaseModel):
    """One document's processing state.

    Carries `user_id` and not the user's email: an operator needs to correlate a
    support request, and a listing of every candidate's address is a mailing
    list. Resolving an id to a person is a separate, separately authorised step
    that does not exist yet.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    user_id: int
    #: completed / processing / waiting_retry / exhausted / failed_permanent / stalled
    state: str
    attempts_used: int
    max_attempts: int

    latest_run_id: int | None = None
    status: str | None = None
    failed_stage: str | None = None
    error_code: str | None = None
    failure_classification: str | None = None

    document_created_at: datetime
    run_created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None
    heartbeat_at: datetime | None = None
    leased: bool = False


class AttentionList(BaseModel):
    """The answer to "show me what needs operational attention"."""

    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentProcessingSummary] = Field(default_factory=list)
    #: How many of each state were found, so an operator can see at a glance
    #: whether this is one bad document or an outage.
    counts: dict[str, int] = Field(default_factory=dict)
    generated_at: datetime


class DocumentProcessingDetail(BaseModel):
    """One document, and every attempt made on it.

    The full history rather than the latest attempt, because the history is the
    diagnosis: three attempts failing on the same provider code is a different
    problem from three failing on different ones.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int
    user_id: int
    content_type: str
    byte_size: int
    document_created_at: datetime
    state: str
    attempts_used: int
    max_attempts: int
    runs: list[ProcessingRunSummary] = Field(default_factory=list)


class ProcessingStageEvent(BaseModel):
    """One stage of one run.

    `detail` is projected to `code` and `classification` only. The column is
    unconstrained JSONB documented as "counts, codes and engine names only" —
    true of every writer today, enforced by nothing.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str
    outcome: str
    duration_ms: int | None = None
    created_at: datetime
    code: str | None = None
    classification: str | None = None


class ProcessingRunDetail(ProcessingRunSummary):
    """One run, with its stage timings."""

    events: list[ProcessingStageEvent] = Field(default_factory=list)
