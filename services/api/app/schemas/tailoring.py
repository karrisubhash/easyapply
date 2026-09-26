"""Tailoring API representations.

`storage_key` is never present in any of these, exactly as it is absent from the
resume schemas. The API does not expose where bytes live, and a client that
cannot name a storage location cannot ask for one — the only route to a file is
`GET /tailored-resumes/{id}/artifact`, which starts from a run id the caller
already owns.

`TailoringRequest` is the only schema untrusted input reaches and it carries two
optional ids, both resolved against the caller server-side. Everything else
about a run — whose it is, which requirements row it pinned, which generator
wrote it — is asserted by the server. `extra="forbid"` makes that structural.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.tailoring import (
    GENERATORS,
    GROUNDING_PASSED,
    GROUNDING_STATUSES,
    SEVERITIES,
    SEVERITY_ERROR,
    STATUS_FAILED,
    TERMINAL_STATUSES,
)
from app.schemas.tailored_resume import TailoredResumeDocument


class TailoringRequest(BaseModel):
    """What a caller may ask for when starting a tailoring run."""

    model_config = ConfigDict(extra="forbid")

    #: Which immutable snapshot to build from. Omitted means the caller's
    #: current confirmed version. Drafts are refused; a version belonging to
    #: another user is a 404 resolved server-side.
    profile_version_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "The confirmed profile version to tailor from. Defaults to the caller's "
            "current confirmed version. Drafts are refused."
        ),
    )

    #: A match to take emphasis from. Optional, and it changes ORDER only — a
    #: requirement the match called missing stays missing, and naming it here
    #: buys no licence to claim it. See tailoring/pipeline.py::_emphasis.
    match_result_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "A match for this job, used to decide what to lead with. Never used to "
            "claim a requirement the profile does not evidence."
        ),
    )


class TailoringIssueRead(BaseModel):
    """One grounding finding, as the review screen shows it."""

    model_config = ConfigDict(from_attributes=True)

    severity: str
    section: str
    field_path: str | None
    #: The generated text this is about. Shown next to the finding so a reader
    #: can judge it rather than take our word for it.
    claim: str | None
    reason: str


class TailoredResumeRead(BaseModel):
    """The generated document and the verdict on it.

    The document is returned even when grounding failed. That is deliberate and
    it is the honest choice: hiding a rejected document would leave the user
    unable to see what was wrong with it, and the artifact — the thing that
    could be sent to an employer — is withheld instead.
    """

    model_config = ConfigDict(from_attributes=True)

    document: TailoredResumeDocument
    schema_version: str
    grounding_status: str
    error_count: int
    warning_count: int
    issues: list[TailoringIssueRead] = Field(default_factory=list)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_approved(self) -> bool:
        """Whether this document may be downloaded.

        Derived on the server so a client cannot invent its own rule. The same
        predicate `service.get_artifact` applies before handing over bytes — a
        UI that disagreed with it would show a download button that 409s.
        """
        return self.grounding_status == GROUNDING_PASSED


class ArtifactRead(BaseModel):
    """What the user is told about the file. Never where it lives."""

    model_config = ConfigDict(from_attributes=True)

    filename: str
    media_type: str
    byte_size: int
    created_at: datetime


class TailoringRunSummary(BaseModel):
    """A tailoring run as it appears in a listing: state, not content."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    profile_version_id: int
    job_requirements_id: int
    match_result_id: int | None
    attempt: int
    status: str
    generator: str
    model: str | None
    prompt_version: str | None
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    # Set by the router from the service's own predicates rather than derived
    # here: `is_stalled` and `can_retry` both read configuration and the clock,
    # and a schema that reached for either would be a second copy of a rule the
    # service already owns.
    is_stalled: bool = False
    can_retry: bool = False

    #: Which version this was built from, as the user names it. The id above is
    #: the database's name for it; a person reading "Version 3" needs this one.
    profile_version_number: int | None = None

    #: Enough of the job to recognise the run in a list without a second call.
    job_title: str | None = None
    company_name: str | None = None

    #: Present once a document exists, whatever its verdict.
    grounding_status: str | None = None
    error_count: int | None = None
    warning_count: int | None = None
    has_artifact: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_terminal(self) -> bool:
        """Whether this attempt has stopped. Everything else is in progress."""
        return self.status in TERMINAL_STATUSES


class TailoringRunDetail(TailoringRunSummary):
    """A run in full, with the document it produced and every finding on it."""

    result: TailoredResumeRead | None = None
    artifact: ArtifactRead | None = None


def tailored_document(payload: dict[str, Any] | None) -> TailoredResumeDocument:
    """Re-validate the stored payload on the way out.

    It was validated on the way in, but it has been through JSONB since; taking
    it back through the model means a response can never be shaped by whatever
    happens to be in the column — the same discipline `structured_result_payload`
    and `job_requirements_payload` apply in their own domains.
    """
    return TailoredResumeDocument.model_validate(payload or {})


#: Re-exported so clients and tests share one spelling of the vocabulary.
TAILORING_GENERATORS = GENERATORS
TAILORING_GROUNDING_STATUSES = GROUNDING_STATUSES
TAILORING_SEVERITIES = SEVERITIES
TAILORING_SEVERITY_ERROR = SEVERITY_ERROR
TAILORING_STATUS_FAILED = STATUS_FAILED
