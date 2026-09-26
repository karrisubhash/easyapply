"""The job domain: a posting, its processing history, and what was extracted.

Three layers, deliberately kept apart, mirroring the raw/normalized/structured
split the resume domain uses between an uploaded file, its extracted text, and
its AI reading:

    Job              the canonical entity. `raw_payload` is exactly what the
                     client submitted, as validated (never re-derived,
                     never overwritten); every other column is the
                     deterministic normalization of that payload — a job
                     title with its whitespace collapsed, an employment type
                     mapped onto our closed vocabulary, a URL with tracking
                     parameters stripped. Immutable once created: the same
                     identity submitted again returns this row unchanged.

    JobProcessingRun  one attempt at turning a Job's description into
                     structured requirements. Append-only, like the resume
                     domain's runs — a retry is a new row, never a rewritten
                     one, so "attempt 1 failed, attempt 2 completed" is a
                     fact the table holds rather than a claim we make.

    JobRequirements   what a structurer (deterministic today, a model later)
                     read out of the description, validated against a
                     schema, with a citation for every item. One row per
                     completed run.

Processing here is synchronous, not leased. Resume's lease/heartbeat/reaper
machinery exists because OCR and a model call can run for seconds and a worker
can die mid-attempt; the deterministic structurer in this milestone is a regex
pass over text already in memory, done before the request that triggered it
returns. `JobProcessingRun` still exists — attempts, timestamps, failure codes,
history — because when a real AI structurer is added later and calls start
taking real time, the exact same row shape is what a lease would attach to. The
column that would not exist yet is `next_retry_at`; nothing about this schema
has to change to add one.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# --- job identity ------------------------------------------------------------
#
# A closed vocabulary WE define, unlike employment_type or workplace_type below
# — those describe how a job's own page phrases itself and vary by site, so
# they are normalized from free text. source_type names which scraper produced
# this payload, which is a fact about our own extension, not about the job
# posting, so an unrecognized value is a malformed request, not a stray label
# to normalize away.
SOURCE_LINKEDIN = "linkedin"
SOURCE_INDEED = "indeed"
SOURCE_GREENHOUSE = "greenhouse"
SOURCE_LEVER = "lever"
SOURCE_WORKDAY = "workday"
SOURCE_COMPANY_CAREERS = "company_careers"
SOURCE_OTHER = "other"

JOB_SOURCE_TYPES = (
    SOURCE_LINKEDIN,
    SOURCE_INDEED,
    SOURCE_GREENHOUSE,
    SOURCE_LEVER,
    SOURCE_WORKDAY,
    SOURCE_COMPANY_CAREERS,
    SOURCE_OTHER,
)

EMPLOYMENT_TYPES = (
    "full_time",
    "part_time",
    "contract",
    "internship",
    "freelance",
    "temporary",
)

WORKPLACE_TYPES = ("remote", "hybrid", "on_site")

SALARY_PERIODS = ("year", "month", "hour")

_SOURCE_SQL = ", ".join(f"'{value}'" for value in JOB_SOURCE_TYPES)
_EMPLOYMENT_SQL = ", ".join(f"'{value}'" for value in EMPLOYMENT_TYPES)
_WORKPLACE_SQL = ", ".join(f"'{value}'" for value in WORKPLACE_TYPES)
_SALARY_PERIOD_SQL = ", ".join(f"'{value}'" for value in SALARY_PERIODS)

# A posting's own text is effectively unbounded; a browser extension scraping a
# live page is not a place to enforce policy, so the ceiling is enforced here,
# at the one edge everything must cross.
MAX_DESCRIPTION_LENGTH = 50_000


class Job(Base):
    """A job posting, as normalized from what the extension submitted.

    Identity is deliberately NOT "the same URL" — see the two partial unique
    indexes below — because a URL alone conflates two different real risks:
    tracking parameters make two fetches of the identical posting look like
    different jobs, while a generic careers-page URL (`/careers`) can host many
    different postings behind client-side routing and would make different jobs
    look identical. Scoped per user throughout, exactly like
    `ResumeDocument.content_sha256`: a global index would let one user's save
    collide with a stranger's, which is an existence oracle dressed up as a
    conflict.
    """

    __tablename__ = "jobs"

    __table_args__ = (
        # Tier 1: the source's own identifier for the posting, when it gave us
        # one. The strongest signal available — LinkedIn's job id means the
        # same thing every time it is scraped, regardless of query string.
        Index(
            "uq_jobs_user_id_source_type_external_job_id",
            "user_id",
            "source_type",
            "external_job_id",
            unique=True,
            postgresql_where=text("external_job_id IS NOT NULL"),
        ),
        # Tier 2: no external id was available, so the normalized URL is what
        # we have. Only applies when tier 1 does not, so a source that
        # sometimes supplies an id and sometimes does not is not penalized for
        # the times it does.
        Index(
            "uq_jobs_user_id_source_type_canonical_url",
            "user_id",
            "source_type",
            "canonical_url",
            unique=True,
            postgresql_where=text("external_job_id IS NULL"),
        ),
        CheckConstraint(f"source_type IN ({_SOURCE_SQL})", name="ck_jobs_source_type"),
        CheckConstraint(
            f"employment_type IS NULL OR employment_type IN ({_EMPLOYMENT_SQL})",
            name="ck_jobs_employment_type",
        ),
        CheckConstraint(
            f"workplace_type IS NULL OR workplace_type IN ({_WORKPLACE_SQL})",
            name="ck_jobs_workplace_type",
        ),
        CheckConstraint(
            f"salary_period IS NULL OR salary_period IN ({_SALARY_PERIOD_SQL})",
            name="ck_jobs_salary_period",
        ),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min",
            name="ck_jobs_salary_max_after_min",
        ),
        CheckConstraint("char_length(job_title) > 0", name="ck_jobs_job_title_not_blank"),
        CheckConstraint(
            f"description_text IS NULL OR char_length(description_text) <= {MAX_DESCRIPTION_LENGTH}",
            name="ck_jobs_description_length",
        ),
        CheckConstraint("char_length(content_hash) = 64", name="ck_jobs_content_hash_length"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # RESTRICT, not CASCADE: erasing a user is a deliberate procedure, not a
    # side effect of a stray DELETE — the same reasoning as every other
    # per-user table in this schema.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_jobs_user_id"),
        nullable=False,
        index=True,
    )

    # --- where this came from --------------------------------------------
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Exactly as submitted. Never used to build a filesystem path — there is
    # none here — but still validated at the edge (scheme, length) before it
    # ever reaches this column, the same posture as a resume's filename.
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Deterministically derived from source_url: lowercase host, tracking
    # parameters stripped, fragment dropped. Always present because
    # source_url always is.
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)

    external_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # sha256 over normalized title+company+description. An indexed hint for
    # future duplicate-detection heuristics, deliberately NOT a uniqueness
    # constraint: two genuinely different postings can share boilerplate text,
    # and collapsing them would be exactly the wrong kind of merge.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # --- what the posting says --------------------------------------------
    job_title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location_raw: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Distinct axes: a role can be full_time AND remote, or contract AND
    # on_site. Conflating them into one field would force a job that is both
    # ("Contract, Remote") into a single slot with no room for the other fact.
    employment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workplace_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    salary_min: Mapped[Any | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[Any | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The ingestion payload exactly as validated, before normalization. Never
    # the unvalidated HTTP body — every field in it already passed type,
    # length and URL-scheme checks — but untouched by the normalization step
    # that produced the columns above, so a later audit can see what the
    # extension actually sent versus what we made of it.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    runs: Mapped[list["JobProcessingRun"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobProcessingRun.attempt",
    )

    @property
    def latest_run(self) -> "JobProcessingRun | None":
        return self.runs[-1] if self.runs else None

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.job_title!r} source={self.source_type!r}>"


# --- processing ----------------------------------------------------------------
#
# Deliberately smaller than the resume state graph. Resume's extra states
# (validating, extracting, ocr_required, ocr_processing, validating_result)
# exist because a binary file goes through several genuinely different
# failure-prone stages before any structuring happens at all. A job posting
# arrives as text the extension already read out of the DOM — there is no
# extraction step to fail — so the graph collapses to: queued, then working on
# it, then done one way or the other.

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

INITIAL_STATUS = STATUS_QUEUED
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_QUEUED: frozenset({STATUS_PROCESSING, STATUS_FAILED}),
    STATUS_PROCESSING: frozenset({STATUS_COMPLETED, STATUS_FAILED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_FAILED: frozenset(),
}

ALL_STATUSES = tuple(ALLOWED_TRANSITIONS)

# Two stages, because they fail for genuinely different reasons: a structurer
# bug versus a database constraint. Not one per requirement category — that
# would multiply the transition table for no diagnostic gain, the same
# reasoning the resume domain used to justify its own stage list.
STAGES = ("structure", "materialise")

_STATUS_SQL = ", ".join(f"'{value}'" for value in ALL_STATUSES)
_STAGE_SQL = ", ".join(f"'{value}'" for value in STAGES)


class JobProcessingRun(Base):
    """One attempt at structuring a job's description.

    No lease columns. Processing runs to completion inside the request that
    started it — there is no worker to die mid-attempt yet — so there is
    nothing for a lease to protect against. When a real AI structurer makes
    this slow enough to move off the request thread, the columns a lease needs
    attach here exactly as they did on `resume_processing_runs`; nothing about
    this table's shape has to change first.
    """

    __tablename__ = "job_processing_runs"

    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_job_processing_runs_job_id_attempt"),
        CheckConstraint("attempt > 0", name="ck_job_processing_runs_attempt_positive"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_job_processing_runs_status"),
        CheckConstraint(
            f"failed_stage IS NULL OR failed_stage IN ({_STAGE_SQL})",
            name="ck_job_processing_runs_failed_stage",
        ),
        CheckConstraint(
            "(status = 'failed') = (failed_stage IS NOT NULL)",
            name="ck_job_processing_runs_failed_stage_matches_status",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="ck_job_processing_runs_error_code_matches_status",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_job_processing_runs_finished_at_matches_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE", name="fk_job_processing_runs_job_id"),
        nullable=False,
        index=True,
    )

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=INITIAL_STATUS)

    failed_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Our own words only, truncated — never str(exception). A structuring bug
    # in a regex library, or a future model SDK, quotes its input in its
    # messages, and the input here is the job description.
    error_message: Mapped[str | None] = mapped_column(String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="runs")

    events: Mapped[list["JobProcessingEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="JobProcessingEvent.id",
    )
    requirements: Mapped["JobRequirements | None"] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def __repr__(self) -> str:
        return f"<JobProcessingRun id={self.id} attempt={self.attempt} status={self.status!r}>"


class JobProcessingEvent(Base):
    """One stage of one run, timed. Mirrors `resume_processing_events` exactly:
    the same observability question — which stage failed, how long did it take
    — deserves the same answer, a query rather than a log search."""

    __tablename__ = "job_processing_events"

    __table_args__ = (
        CheckConstraint(f"stage IN ({_STAGE_SQL})", name="ck_job_processing_events_stage"),
        CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'skipped')",
            name="ck_job_processing_events_outcome",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_job_processing_events_duration_not_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    job_processing_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "job_processing_runs.id",
            ondelete="CASCADE",
            name="fk_job_processing_events_run_id",
        ),
        nullable=False,
        index=True,
    )

    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Never the job description. Counts, codes and structurer names only.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["JobProcessingRun"] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<JobProcessingEvent {self.stage}:{self.outcome} {self.duration_ms}ms>"


class JobRequirements(Base):
    """What a structurer read out of one run's job description.

    One row per completed run, holding the full validated structure — required
    and preferred skills, experience, education, certifications,
    responsibilities, technologies, qualifications — each item carrying its own
    citation into the description. Stored as JSONB rather than exploded into
    one child table per category: nothing in this milestone queries a single
    requirement row in isolation, and matching, when it is built, reads the
    whole structure at once to compare it against a profile. Six additional
    tables today would be schema nobody uses until then; the JSON keeps every
    fact matching will need without inventing indexes for questions nobody is
    asking yet.

    Never populated directly from a structurer's raw output — see
    `app/services/job/structuring.py` — so this row is always the validated
    shape (`JobCandidate`), never whatever a future model happened to emit.
    """

    __tablename__ = "job_requirements"

    __table_args__ = (
        UniqueConstraint(
            "job_processing_run_id", name="uq_job_requirements_run_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    job_processing_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "job_processing_runs.id",
            ondelete="CASCADE",
            name="fk_job_requirements_run_id",
        ),
        nullable=False,
    )

    # Which structurer produced this — "deterministic" today. The columns
    # below exist for the provider that does not exist yet, exactly as
    # resume_structured_results carries them ahead of AnthropicProvider.
    structurer: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["JobProcessingRun"] = relationship(back_populates="requirements")

    def __repr__(self) -> str:
        return f"<JobRequirements run={self.job_processing_run_id} by={self.structurer!r}>"
