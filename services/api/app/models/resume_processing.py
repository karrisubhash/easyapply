"""The processing attempt, its stages, and what it produced."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# --- the state machine -------------------------------------------------------
#
# One `failed` state with a `failed_stage` column, rather than a failure state
# per stage: every failure has identical successor semantics (none — it is
# terminal and the retry is a new run), so per-stage failure states would
# multiply the transition table for no expressive gain.

STATUS_UPLOADED = "uploaded"
STATUS_VALIDATING = "validating"
STATUS_EXTRACTING = "extracting"
STATUS_OCR_REQUIRED = "ocr_required"
STATUS_OCR_PROCESSING = "ocr_processing"
STATUS_STRUCTURING = "structuring"
STATUS_VALIDATING_RESULT = "validating_result"
STATUS_MATERIALISING = "materialising"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: The only state a run may be created in.
INITIAL_STATUS = STATUS_UPLOADED

#: Nothing leaves these.
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})

#: A run in one of these has stopped making progress and can be retried — which
#: always means a NEW run, never reopening this one.
RETRYABLE_STATUSES = frozenset({STATUS_FAILED})

#: The whole legal graph. Enforced twice: here, and by a database trigger, so a
#: future worker cannot bypass it by writing the table directly.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_UPLOADED: frozenset({STATUS_VALIDATING, STATUS_FAILED}),
    STATUS_VALIDATING: frozenset({STATUS_EXTRACTING, STATUS_FAILED}),
    # Extraction either produced usable text, or did not and OCR is the fallback.
    STATUS_EXTRACTING: frozenset({STATUS_STRUCTURING, STATUS_OCR_REQUIRED, STATUS_FAILED}),
    STATUS_OCR_REQUIRED: frozenset({STATUS_OCR_PROCESSING, STATUS_FAILED}),
    STATUS_OCR_PROCESSING: frozenset({STATUS_STRUCTURING, STATUS_FAILED}),
    STATUS_STRUCTURING: frozenset({STATUS_VALIDATING_RESULT, STATUS_FAILED}),
    STATUS_VALIDATING_RESULT: frozenset({STATUS_MATERIALISING, STATUS_FAILED}),
    STATUS_MATERIALISING: frozenset({STATUS_COMPLETED, STATUS_FAILED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_FAILED: frozenset(),
}

ALL_STATUSES = tuple(ALLOWED_TRANSITIONS)

#: Stage names used by failed_stage and by the event log.
STAGES = (
    "validate",
    "extract",
    "ocr",
    "structure",
    "validate_result",
    "materialise",
)

_STATUS_SQL = ", ".join(f"'{value}'" for value in ALL_STATUSES)
_STAGE_SQL = ", ".join(f"'{value}'" for value in STAGES)


class ResumeProcessingRun(Base):
    """One attempt at turning a document into a profile draft.

    Runs are append-only. A retry is a new row with the next `attempt`, never a
    reset of an existing one, which keeps the state graph acyclic and makes
    "how many times did this fail" a COUNT.

    There is deliberately NO "only one active run" constraint. A killed worker
    leaves a run stranded in a non-terminal state; a uniqueness rule over active
    runs would then wedge the document forever with no way back. The lease below
    is the mechanism that recovers such a run instead.

    The lease is also what makes this table the work queue. A run that is not
    terminal and not leased IS queued work, so there is no second table that
    could disagree with this one about what remains to be done.
    """

    __tablename__ = "resume_processing_runs"

    __table_args__ = (
        UniqueConstraint(
            "resume_document_id",
            "attempt",
            name="uq_resume_processing_runs_document_id_attempt",
        ),
        CheckConstraint("attempt > 0", name="ck_resume_processing_runs_attempt_positive"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_resume_processing_runs_status"),
        CheckConstraint(
            f"failed_stage IS NULL OR failed_stage IN ({_STAGE_SQL})",
            name="ck_resume_processing_runs_failed_stage",
        ),
        # A failure names where it happened and why; a success claims neither.
        CheckConstraint(
            "(status = 'failed') = (failed_stage IS NOT NULL)",
            name="ck_resume_processing_runs_failed_stage_matches_status",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name="ck_resume_processing_runs_error_code_matches_status",
        ),
        # Terminal runs have stopped; running ones have not.
        CheckConstraint(
            "(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)",
            name="ck_resume_processing_runs_finished_at_matches_status",
        ),
        # Only a completed run may claim to have produced a profile version.
        CheckConstraint(
            "profile_version_id IS NULL OR status = 'completed'",
            name="ck_resume_processing_runs_version_only_when_completed",
        ),
        # An owner and an expiry are what a lease is; half of one is not a lease.
        CheckConstraint(
            "(lease_owner IS NULL) = (leased_until IS NULL)",
            name="ck_resume_processing_runs_lease_paired",
        ),
        # There is deliberately NO constraint that next_retry_at >= created_at.
        # It would compare an application clock against the database's own
        # server_default, and at high jitter the delay can round to almost
        # nothing — so ordinary clock skew between the two would reject the
        # INSERT and destroy the retry outright. The thing it would defend
        # against is harmless anyway: a time already in the past simply reads as
        # due now, which is exactly what it means.
        #
        # The claim query and the reaper both read "not terminal, ordered by
        # lease expiry". Partial, because terminal runs are the vast majority
        # and are never claimable.
        Index(
            "ix_resume_processing_runs_claimable",
            "leased_until",
            "id",
            postgresql_where=text("status NOT IN ('completed', 'failed')"),
        ),
        # "Which scheduled retries have come due?" — asked by the reaper on
        # every sweep, against a table where almost every row is terminal and
        # almost every non-terminal row has no retry time at all.
        Index(
            "ix_resume_processing_runs_due_retry",
            "next_retry_at",
            postgresql_where=text(
                "status NOT IN ('completed', 'failed') AND next_retry_at IS NOT NULL"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    resume_document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "resume_documents.id",
            ondelete="CASCADE",
            name="fk_resume_processing_runs_document_id",
        ),
        nullable=False,
    )

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=INITIAL_STATUS
    )

    failed_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Counts, not content. These survive a purge of the extracted text, so the
    # question "what did this run actually read?" stays answerable without
    # retaining the resume itself.
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # RESTRICT, not SET NULL: a referential action would be a real UPDATE on
    # this row, which the transition trigger refuses on a terminal run.
    profile_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "profile_versions.id",
            ondelete="RESTRICT",
            name="fk_resume_processing_runs_profile_version_id",
        ),
        nullable=True,
        index=True,
    )

    # --- the lease -----------------------------------------------------------
    #
    # Ownership lives in the database, not in worker memory, because the
    # question "is anyone still working on this?" has to be answerable by a
    # process that is not the one working on it — and by one that started after
    # the original worker was killed.
    #
    # An opaque worker identity. Never a hostname alone: two containers from the
    # same image would then claim the same name.
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # When the claim lapses. A worker that has not renewed by then is treated as
    # gone, whatever it believes about itself.
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Last renewal. Kept after the lease is released, so "when did this run last
    # show a sign of life" survives the run finishing.
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- the backoff ---------------------------------------------------------
    #
    # When this run becomes eligible to start. NULL means "nothing is holding it
    # back", which is the ordinary case: a first attempt, or a retry with
    # backoff switched off.
    #
    # It lives on the run that will WAIT, not on the failure that caused the
    # wait, because the failed run is terminal and its row is the record of what
    # happened — writing a future intention onto it would be editing history.
    # A successor row carrying its own start time also survives a restart,
    # is visible to every process, and needs no timer anywhere.
    #
    # This is the whole of the backoff mechanism. There is deliberately no
    # sleeping worker and no delayed message: a worker that fails a run writes
    # this and lets go, and the run is simply not claimable until the moment it
    # names.
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped["ResumeDocument"] = relationship(back_populates="runs")  # noqa: F821

    events: Mapped[list["ResumeProcessingEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ResumeProcessingEvent.id",
    )
    pages: Mapped[list["ResumeDocumentPage"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ResumeDocumentPage.page_number",
    )
    structured_result: Mapped["ResumeStructuredResult | None"] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_leased_at(self, moment: datetime) -> bool:
        """Whether someone holds this run at `moment`.

        Takes the moment rather than reading the clock, so the caller and the
        database can be asked about the same instant.
        """
        return self.leased_until is not None and self.leased_until > moment

    def is_due_at(self, moment: datetime) -> bool:
        """Whether this run's backoff has elapsed.

        The mirror of the condition the claim query applies, so Python and SQL
        cannot drift on what "due" means.
        """
        return self.next_retry_at is None or self.next_retry_at <= moment

    def __repr__(self) -> str:
        return f"<ResumeProcessingRun id={self.id} attempt={self.attempt} status={self.status!r}>"


class ResumeProcessingEvent(Base):
    """One stage of one run: when it began, when it ended, and how it went.

    This is the observability record. It exists as rows rather than log lines
    because the questions it answers — which stage failed, how long each stage
    took — are queries, and a log search is not a query.
    """

    __tablename__ = "resume_processing_events"

    __table_args__ = (
        CheckConstraint(f"stage IN ({_STAGE_SQL})", name="ck_resume_processing_events_stage"),
        CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'skipped')",
            name="ck_resume_processing_events_outcome",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_resume_processing_events_duration_not_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    resume_processing_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "resume_processing_runs.id",
            ondelete="CASCADE",
            name="fk_resume_processing_events_run_id",
        ),
        nullable=False,
        index=True,
    )

    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Never the resume's content. Counts, codes and engine names only.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["ResumeProcessingRun"] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<ResumeProcessingEvent {self.stage}:{self.outcome} {self.duration_ms}ms>"


class ResumeDocumentPage(Base):
    """The extracted text of one page of one run.

    Its own table for two reasons. Text is keyed to the RUN, not the document,
    because a re-run with OCR legitimately produces different text for the same
    page. And it is the single place verbatim resume text lives, so a routine
    SELECT of a run cannot drag PII into a log line, an exception repr or a
    trace — and a retention purge is one DELETE that rewrites no audit row.
    """

    __tablename__ = "resume_document_pages"

    __table_args__ = (
        UniqueConstraint(
            "resume_processing_run_id",
            "page_number",
            name="uq_resume_document_pages_run_id_page_number",
        ),
        CheckConstraint("page_number > 0", name="ck_resume_document_pages_page_number_positive"),
        CheckConstraint(
            "extraction_method IN ('text', 'ocr')",
            name="ck_resume_document_pages_extraction_method",
        ),
        CheckConstraint("char_count >= 0", name="ck_resume_document_pages_char_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    resume_processing_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "resume_processing_runs.id",
            ondelete="CASCADE",
            name="fk_resume_document_pages_run_id",
        ),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Nullable so a retention purge can null the text while leaving the page
    # row — and therefore the page count and provenance targets — intact.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Per page, not per document: a resume whose first page is a scan and whose
    # second page is real text has two different answers here.
    extraction_method: Mapped[str] = mapped_column(String(16), nullable=False)

    extraction_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    run: Mapped["ResumeProcessingRun"] = relationship(back_populates="pages")

    def __repr__(self) -> str:
        return f"<ResumeDocumentPage page={self.page_number} method={self.extraction_method!r}>"


class ResumeStructuredResult(Base):
    """What the AI produced, after schema validation.

    Separate from the run for the same reason as the page text: it contains the
    candidate's personal details, and keeping it out of the run row keeps it out
    of every routine read. One row per run.

    `payload` holds the validated candidate document INCLUDING its per-field
    provenance. Provenance is not stored against the profile snapshot, because
    the snapshot's collections are re-sorted on read and any index-based pointer
    into them would silently rot; the review UI reads provenance from here.
    """

    __tablename__ = "resume_structured_results"

    __table_args__ = (
        UniqueConstraint(
            "resume_processing_run_id",
            name="uq_resume_structured_results_run_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    resume_processing_run_id: Mapped[int] = mapped_column(
        ForeignKey(
            "resume_processing_runs.id",
            ondelete="CASCADE",
            name="fk_resume_structured_results_run_id",
        ),
        nullable=False,
    )

    # --- attribution ---------------------------------------------------------
    #
    # Enough to answer "what produced this reading, and would the same inputs
    # produce it again?" months later. A model is retired, a prompt is reworded
    # and a schema gains a field; without these, an old result becomes a claim
    # with no provenance of its own.
    #
    # Which structurer ran — deterministic, or a provider-backed one.
    structurer: Mapped[str] = mapped_column(String(64), nullable=False)
    # The provider adapter behind it, when there was one. NULL for deterministic.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The candidate schema the payload was validated against.
    schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The prompt template that produced it. The text itself is in the source
    # tree under this version; storing the prompt here would put the resume's
    # own words into a second place.
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Counts, identifiers and timings from the provider — token usage, stop
    # reason, latency. Never the prompt, never the completion, never a key.
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # What could not be mapped into the profile snapshot and why — for example
    # an experience the resume never dated, which the snapshot cannot represent.
    # Recorded rather than dropped, so the review screen can say so.
    unmapped: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["ResumeProcessingRun"] = relationship(back_populates="structured_result")

    def __repr__(self) -> str:
        return f"<ResumeStructuredResult run={self.resume_processing_run_id} by={self.structurer!r}>"
