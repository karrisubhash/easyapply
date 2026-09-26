"""Job API representations.

`JobIngestionRequest` is the one schema in this file that untrusted input ever
reaches. It is deliberately narrow — no `status`, no `user_id`, nothing the
caller could use to assert a fact only the server is allowed to assert. The
authenticated user comes from `CurrentUser` the same way it does everywhere
else in this API; a job's processing status is computed from its own
`JobProcessingRun` history, never accepted as an opinion from the client.
`extra="forbid"` is what makes both of those refusals structural rather than a
check someone has to remember to write: a payload naming either field is
rejected before a service function ever runs.
"""

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.job import (
    JOB_SOURCE_TYPES,
    MAX_DESCRIPTION_LENGTH,
    STATUS_FAILED,
    TERMINAL_STATUSES,
)
from app.schemas.job_candidate import JobCandidate
from app.services.job.failures import is_user_retryable

JobSourceType = Literal[
    "linkedin", "indeed", "greenhouse", "lever", "workday", "company_careers", "other"
]

#: Schemes a job posting URL may use. Everything else — javascript:, data:,
#: file:, a bare string with no scheme — is refused outright: this field is
#: never rendered as a link without also being one of these, and an extension
#: is exactly the kind of caller that should not be trusted to have gotten
#: this right upstream.
_ALLOWED_URL_SCHEMES = ("http", "https")
_MAX_URL_LENGTH = 2048


def _validate_job_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("A job URL is required.")
    if len(candidate) > _MAX_URL_LENGTH:
        raise ValueError(f"The URL is longer than {_MAX_URL_LENGTH} characters.")
    if any(ord(char) < 0x20 for char in candidate):
        raise ValueError("The URL contains control characters.")

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise ValueError("Only http and https job URLs are accepted.")
    if not parts.netloc:
        raise ValueError("The URL has no host.")
    return candidate


def _clean_label(value: str) -> str:
    """Collapse whitespace and drop control characters from a short field.

    For single-line labels — a title, a company name, an external id — not for
    `description_text`, which keeps its own line breaks because it is meant to
    still read as the posting it came from.
    """
    stripped = "".join(char for char in value if ord(char) >= 0x20 or char in "\n\t")
    return " ".join(stripped.split())


class JobIngestionRequest(BaseModel):
    """A job posting as an extension would submit it.

    Every field here is validated at the edge — type, length, URL scheme — but
    nothing is yet NORMALIZED: `employment_type` arrives as whatever text the
    source site used ("Full-time", "FULL_TIME", "Contract - W2"), not yet
    mapped to the closed vocabulary `Job.employment_type` enforces. That
    mapping is `app/services/job/normalization.py`'s job, deliberately kept
    separate from validation — validation decides whether a payload is
    well-formed at all; normalization decides what a well-formed field means.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: JobSourceType
    source_url: str
    external_job_id: str | None = Field(default=None, max_length=255)

    job_title: str = Field(max_length=500)
    company_name: str | None = Field(default=None, max_length=500)
    location_raw: str | None = Field(default=None, max_length=500)

    # Free text: the source site's own phrasing. Normalized later, onto
    # Job.employment_type / Job.workplace_type's closed vocabulary.
    employment_type: str | None = Field(default=None, max_length=64)
    workplace_type: str | None = Field(default=None, max_length=64)

    description_text: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)

    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    salary_period: str | None = Field(default=None, max_length=32)

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        return _validate_job_url(value)

    @field_validator("job_title")
    @classmethod
    def _validate_job_title(cls, value: str) -> str:
        cleaned = _clean_label(value)
        if not cleaned:
            raise ValueError("A job title is required.")
        return cleaned

    @field_validator("company_name", "location_raw", "external_job_id")
    @classmethod
    def _clean_optional_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_label(value)
        return cleaned or None

    @field_validator("salary_currency")
    @classmethod
    def _uppercase_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned.isalpha():
            raise ValueError("salary_currency must be a 3-letter currency code.")
        return cleaned

    @field_validator("description_text")
    @classmethod
    def _strip_dangerous_control_characters(cls, value: str | None) -> str | None:
        """Only NUL and other C0 controls are stripped here.

        Everything else about the text is left alone — this field exists so a
        later reader can trust it still reads as the posting it came from, and
        the meaningful defense against it being treated as anything other than
        inert text is that nothing in this application ever renders it as HTML
        or feeds it to a model as instructions rather than data (see
        `app/services/job/structuring.py`), not scrubbing the text itself.
        """
        if value is None:
            return None
        return "".join(char for char in value if ord(char) >= 0x20 or char in "\n\r\t")


# --- reading it back ----------------------------------------------------------


class JobProcessingRunRead(BaseModel):
    """One processing attempt, as the owning user sees it.

    Exposing `error_message` here is safe in a way it would not be on a
    surface reading across users: this is the same person who submitted the
    job, asking about their own run.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt: int
    status: str
    failed_stage: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_terminal(self) -> bool:
        """Whether this attempt has stopped. Everything else is in progress."""
        return self.status in TERMINAL_STATUSES

    # Derived, not stored. The classification rule lives in one place on the
    # server (app/services/job/failures.py); a client that re-derived it from
    # error_code would be a second copy free to disagree. Mirrors
    # ResumeProcessingRunRead.can_retry.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_retry(self) -> bool:
        """Whether offering the user a retry button would be honest.

        False for a failure the same job text and configuration will
        reproduce — an AI provider's content refusal does not change on the
        second attempt, and a button that says otherwise is a lie.
        """
        if self.status != STATUS_FAILED:
            return False
        # The same rule `service.require_retryable` enforces — including the
        # configuration failures an operator can cure — so the button this
        # drives and the endpoint behind it cannot disagree.
        return is_user_retryable(self.error_code)


class JobSummary(BaseModel):
    """A job as it appears in a listing — enough to recognize it and its
    processing state, not the full description."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    source_url: str
    job_title: str
    company_name: str | None
    location_raw: str | None
    employment_type: str | None
    workplace_type: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    created_at: datetime
    latest_run: JobProcessingRunRead | None = None


class JobDetail(JobSummary):
    """A single job in full — including the description as normalized, which
    is the closest thing to "raw" this API exposes: the posting's own text,
    untouched by any structurer."""

    external_job_id: str | None
    canonical_url: str
    description_text: str | None
    updated_at: datetime


class JobRequirementsRead(BaseModel):
    """The structured reading of one job, for the review screen."""

    model_config = ConfigDict(from_attributes=True)

    structurer: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    schema_version: str | None
    candidate: JobCandidate
    created_at: datetime


def job_requirements_payload(payload: dict[str, Any] | None) -> JobCandidate:
    """Re-validate the stored payload on the way out.

    It was validated on the way in, but it has been through JSONB since;
    taking it back through the model means a response can never be shaped by
    whatever happens to be in the column — the same discipline as
    `structured_result_payload` in the resume domain.
    """
    return JobCandidate.model_validate(payload or {})


class JobRequirementsResponse(BaseModel):
    """Everything the review screen needs about one job's newest reading.

    `requirements` is the newest reading that exists — the one matching and
    tailoring use — and `run` is the attempt that produced it. After a failed
    reprocess that is an EARLIER attempt than `job.latest_run`, which still
    reports the failure: `run.id != job.latest_run.id` is the signal. When no
    attempt has produced a reading yet, `requirements` is null and `run` is
    the latest attempt, so a job still processing, or whose every attempt
    failed, still shows its state.
    """

    job: JobSummary
    run: JobProcessingRunRead | None
    requirements: JobRequirementsRead | None = None
