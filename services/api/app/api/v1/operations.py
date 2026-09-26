"""Internal operator routes.

Not a product feature. These exist to answer one question — which resume
processing needs a person — and they are the only routes in the application that
read across users, so every one of them sits behind `CurrentOperator` rather
than `CurrentUser`.

Two rules run through the whole module:

1. The responses are assembled field by field from `app/schemas/operations.py`.
   None of the existing resume schemas is reused: every one of them already
   carries something an operator must not see across users — a filename, an
   error message, page text, a structured payload.

2. Reads are not scoped to the caller, so the data minimisation has to be. An
   operator gets ids, counts, timestamps and codes from a closed vocabulary.
   Anything that would let them read a candidate's resume is absent by
   construction rather than filtered out downstream.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.api.operator_identity import CurrentOperator
from app.core.config import settings
from app.models import ResumeStructuredResult
from app.schemas.operations import (
    AttentionList,
    DocumentProcessingDetail,
    DocumentProcessingSummary,
    ProcessingRunDetail,
    ProcessingRunSummary,
    ProcessingStageEvent,
)
from app.services.resume import operations

router = APIRouter(tags=["operations"], prefix="/internal/resume-processing")


def _summary(state: operations.DocumentProcessingState) -> DocumentProcessingSummary:
    return DocumentProcessingSummary(
        document_id=state.document_id,
        user_id=state.user_id,
        state=state.state,
        attempts_used=state.attempts_used,
        max_attempts=state.max_attempts,
        latest_run_id=state.latest_run_id,
        status=state.status,
        failed_stage=state.failed_stage,
        error_code=state.error_code,
        failure_classification=state.failure_classification,
        document_created_at=state.document_created_at,
        run_created_at=state.run_created_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        next_retry_at=state.next_retry_at,
        heartbeat_at=state.heartbeat_at,
        leased=state.leased,
    )


def _run_summary(db, run, *, now: datetime) -> ProcessingRunSummary:
    """One run, projected.

    The structured result is read by an explicit query for four scalar columns.
    Loading `run.structured_result` would pull `payload` — a verbatim copy of
    the resume, inside every provenance quote — into memory one attribute access
    away from being serialised.
    """
    attribution = db.execute(
        ResumeStructuredResult.__table__.select()
        .with_only_columns(
            ResumeStructuredResult.structurer,
            ResumeStructuredResult.provider,
            ResumeStructuredResult.model,
            ResumeStructuredResult.prompt_version,
            ResumeStructuredResult.schema_version,
            ResumeStructuredResult.provider_metadata,
        )
        .where(ResumeStructuredResult.resume_processing_run_id == run.id)
    ).first()

    return ProcessingRunSummary(
        id=run.id,
        document_id=run.resume_document_id,
        attempt=run.attempt,
        max_attempts=settings.RESUME_MAX_ATTEMPTS,
        status=run.status,
        failed_stage=run.failed_stage,
        error_code=run.error_code,
        failure_classification=(
            operations.classification(run.error_code) if run.error_code else None
        ),
        page_count=run.page_count,
        extracted_char_count=run.extracted_char_count,
        ocr_page_count=run.ocr_page_count,
        profile_version_id=run.profile_version_id,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        next_retry_at=run.next_retry_at,
        heartbeat_at=run.heartbeat_at,
        leased=run.is_leased_at(now),
        structurer=attribution.structurer if attribution else None,
        provider=attribution.provider if attribution else None,
        model=attribution.model if attribution else None,
        prompt_version=attribution.prompt_version if attribution else None,
        schema_version=attribution.schema_version if attribution else None,
        provider_metadata=operations.safe_provider_metadata(
            attribution.provider_metadata if attribution else None
        ),
    )


@router.get("/attention", response_model=AttentionList)
def list_attention(
    db: DbSession,
    operator: CurrentOperator,
    limit: int = Query(default=100, ge=1, le=500),
    include_all: bool = Query(
        default=False,
        description="Return every document's state, not only the ones needing attention.",
    ),
):
    """Resume processing that needs operational attention.

    By default: exhausted retry budgets, permanent failures, and runs nobody is
    working on. Healthy documents are left out — a view that listed everything
    would bury the handful that matter, which is the failure mode of most
    operational dashboards.

    `counts` covers every state regardless of the filter, so one glance
    distinguishes a single bad document from an outage.
    """
    now = datetime.now(timezone.utc)
    everything = operations.document_states(db, limit=10_000, now=now)

    counts: dict[str, int] = {}
    for state in everything:
        counts[state.state] = counts.get(state.state, 0) + 1

    selected = [
        state
        for state in everything
        if include_all or state.state in operations.NEEDS_ATTENTION
    ][:limit]

    return AttentionList(
        documents=[_summary(state) for state in selected],
        counts=counts,
        generated_at=now,
    )


@router.get("/documents/{document_id}", response_model=DocumentProcessingDetail)
def read_document(document_id: int, db: DbSession, operator: CurrentOperator):
    """Every attempt made on one document.

    Not scoped to an owner: an operator investigating a failure has been given a
    document id by someone, and refusing it because it belongs to a different
    user would make the endpoint useless for its only purpose.
    """
    now = datetime.now(timezone.utc)
    document, runs = operations.document_attempts(db, document_id=document_id)
    latest = runs[-1] if runs else None

    return DocumentProcessingDetail(
        document_id=document.id,
        user_id=document.user_id,
        content_type=document.content_type,
        byte_size=document.byte_size,
        document_created_at=document.created_at,
        state=operations.derive_state(latest, attempts_used=len(runs), now=now),
        attempts_used=len(runs),
        max_attempts=settings.RESUME_MAX_ATTEMPTS,
        runs=[_run_summary(db, run, now=now) for run in runs],
    )


@router.get("/runs/{run_id}", response_model=ProcessingRunDetail)
def read_run(run_id: int, db: DbSession, operator: CurrentOperator):
    """One attempt, with its stage timings.

    Event `detail` is projected to `code` and `classification`. The column is
    unconstrained JSONB documented as counts and codes only — true of every
    writer today and enforced by nothing, which is not a guarantee worth
    extending across users.
    """
    now = datetime.now(timezone.utc)
    run = operations.get_run(db, run_id=run_id)
    summary = _run_summary(db, run, now=now)

    return ProcessingRunDetail(
        **summary.model_dump(),
        events=[
            ProcessingStageEvent(
                stage=event.stage,
                outcome=event.outcome,
                duration_ms=event.duration_ms,
                created_at=event.created_at,
                code=(event.detail or {}).get("code"),
                classification=(event.detail or {}).get("classification"),
            )
            for event in run.events
        ],
    )
