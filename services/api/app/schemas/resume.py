"""Resume API representations.

`storage_key` is never present in any of these. The API does not expose where
bytes live, and a client that cannot name a storage location cannot ask for one.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.resume_processing import (  # noqa: F401
    ALL_STATUSES,
    STAGES,
    STATUS_FAILED,
    TERMINAL_STATUSES,
)
from app.services.resume.failures import may_retry_manually
from app.schemas.resume_candidate import ResumeCandidate, UnmappedItem


class ProcessingEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stage: str
    outcome: str
    duration_ms: int | None
    created_at: datetime


class ProcessingRunRead(BaseModel):
    """One processing attempt, as the UI sees it.

    Deliberately absent: the lease. Who holds a run and until when is an
    operational fact, and exposing it would invite a client to reason about
    worker scheduling — which is exactly the coupling the lease exists to avoid.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt: int
    status: str
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    page_count: int | None
    extracted_char_count: int | None
    ocr_page_count: int | None
    profile_version_id: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    # When this attempt becomes eligible to run. Present because without it a
    # user whose retry is waiting out a backoff sees an indistinguishable
    # "Queued" and concludes nothing is happening — the wait is deliberate, and
    # a UI that cannot say so makes a designed delay look like a hang.
    next_retry_at: datetime | None = None

    # Derived, not stored. The classification rule lives in one place on the
    # server; a client that re-derived it from error_code would be a second copy
    # free to disagree.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_terminal(self) -> bool:
        """Whether this attempt has stopped. Everything else is in progress."""
        return self.status in TERMINAL_STATUSES

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_waiting_to_retry(self) -> bool:
        """Whether this attempt exists but is deliberately not started yet.

        The difference between "a worker is reading your resume" and "we are
        waiting before trying again", which are the same `status` and would
        otherwise be the same thing on screen.
        """
        if self.status in TERMINAL_STATUSES or self.started_at is not None:
            return False
        return self.next_retry_at is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_retry(self) -> bool:
        """Whether offering the user a retry button would be honest.

        False while a run is in flight, and false for a failure the same bytes
        will reproduce — a corrupt file does not become readable on the second
        attempt, and a button that says otherwise is a lie. True for a failure
        the deployment caused (no OCR engine, a revoked key): hiding the button
        there left the document with no way forward once the deployment was
        fixed. Mirrors `retry.manual_retry_refusal`, from the same rule.
        """
        if self.status != STATUS_FAILED:
            return False
        return may_retry_manually(self.error_code)


class ResumeDocumentRead(BaseModel):
    """An uploaded resume, with the state of its most recent attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    content_type: str
    byte_size: int
    created_at: datetime
    latest_run: ProcessingRunRead | None = None


class ResumeDocumentDetail(ResumeDocumentRead):
    events: list[ProcessingEventRead] = []


class ExtractedPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    char_count: int
    extraction_method: str


class StructuredResultRead(BaseModel):
    """The AI reading, for the review screen.

    Includes provenance, because the point of the review step is that a human
    can check a claim against the page it came from.
    """

    model_config = ConfigDict(from_attributes=True)

    structurer: str
    model: str | None
    candidate: ResumeCandidate
    unmapped: list[UnmappedItem] = []
    pages: list[ExtractedPageRead] = []
    profile_version_number: int | None = None


class ProcessingResultRead(BaseModel):
    """Everything the review screen needs about one run."""

    document: ResumeDocumentRead
    run: ProcessingRunRead
    result: StructuredResultRead | None = None


def structured_result_payload(payload: dict[str, Any] | None) -> ResumeCandidate:
    """Re-validate the stored payload on the way out.

    It was validated on the way in, but it has been through JSONB since; taking
    it back through the model means a response can never be shaped by whatever
    happens to be in the column.
    """
    return ResumeCandidate.model_validate(payload or {})
