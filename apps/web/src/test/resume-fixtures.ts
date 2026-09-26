import type {
  ProcessingResult,
  ProcessingRun,
  ResumeDocument,
  ResumeDocumentDetail,
  StructuredResult,
} from "@easyapply/shared";

/**
 * A completed run by default.
 *
 * `is_terminal` and `can_retry` are computed by the API, not stored, so a
 * fixture that set them independently of `status` could describe a run the
 * server can never produce. They are derived here for the same reason.
 */
export function run(overrides: Partial<ProcessingRun> = {}): ProcessingRun {
  const base = {
    id: 1,
    attempt: 1,
    status: "completed",
    failed_stage: null,
    error_code: null,
    error_message: null,
    page_count: 2,
    extracted_char_count: 345,
    ocr_page_count: 0,
    profile_version_id: 11,
    created_at: "2026-09-18T09:00:00Z",
    started_at: "2026-09-18T09:00:01Z",
    finished_at: "2026-09-18T09:00:04Z",
    ...overrides,
  };

  const terminal = base.status === "completed" || base.status === "failed";

  return {
    ...base,
    is_terminal: terminal,
    // The server's `may_retry_manually`: a transient failure, or one the
    // deployment caused (no OCR engine, a revoked key) — never one the
    // document itself causes.
    can_retry:
      base.status === "failed" &&
      (TRANSIENT_ERROR_CODES.includes(base.error_code ?? "") ||
        DEPLOYMENT_ERROR_CODES.includes(base.error_code ?? "")),
    // Scheduled but not started: the run exists and is deliberately waiting.
    is_waiting_to_retry:
      !terminal && base.started_at === null && base.next_retry_at != null,
    ...overrides,
  };
}

/** Mirrors the transient set in services/api/app/services/resume/failures.py. */
const TRANSIENT_ERROR_CODES = [
  "stored_object_missing",
  "worker_abandoned",
  "unexpected_error",
  "draft_creation_failed",
  "ai_provider_unavailable",
  "ai_timeout",
  "ai_rate_limited",
  "ai_provider_error",
  "ai_output_malformed",
];

/**
 * Mirrors DEPLOYMENT_ERROR_CODES in services/api/app/services/resume/failures.py:
 * permanent for the automatic policy, but a person may retry them once the
 * deployment is fixed, so the server returns `can_retry: true` for them.
 */
const DEPLOYMENT_ERROR_CODES = [
  "ocr_unavailable",
  "ai_not_configured",
  "ai_unauthorized",
  "ai_authentication_failed",
  "ai_billing_blocked",
  "structuring_failed",
  "result_schema_invalid",
  "ai_request_invalid",
  "ai_output_invalid",
];

/** A retry that has been scheduled and is waiting out its backoff. */
export function waitingRetryRun(overrides: Partial<ProcessingRun> = {}): ProcessingRun {
  return queuedRun({
    attempt: 2,
    next_retry_at: "2026-09-18T09:05:00Z",
    ...overrides,
  });
}

/** A run that is queued and has not been picked up by a worker yet. */
export function queuedRun(overrides: Partial<ProcessingRun> = {}): ProcessingRun {
  return run({
    status: "uploaded",
    page_count: null,
    extracted_char_count: null,
    ocr_page_count: null,
    profile_version_id: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  });
}

export function resumeDocument(overrides: Partial<ResumeDocument> = {}): ResumeDocument {
  return {
    id: 1,
    original_filename: "resume.pdf",
    content_type: "application/pdf",
    byte_size: 24_576,
    created_at: "2026-09-18T09:00:00Z",
    latest_run: run(),
    ...overrides,
  };
}

export function resumeDetail(
  overrides: Partial<ResumeDocumentDetail> = {},
): ResumeDocumentDetail {
  return {
    ...resumeDocument(),
    events: [
      { stage: "validate", outcome: "succeeded", duration_ms: 1, created_at: "2026-09-18T09:00:01Z" },
      { stage: "extract", outcome: "succeeded", duration_ms: 380, created_at: "2026-09-18T09:00:02Z" },
    ],
    ...overrides,
  };
}

export function structuredResult(overrides: Partial<StructuredResult> = {}): StructuredResult {
  return {
    structurer: "deterministic",
    model: null,
    profile_version_number: 3,
    pages: [
      { page_number: 1, char_count: 200, extraction_method: "text" },
      { page_number: 2, char_count: 145, extraction_method: "text" },
    ],
    unmapped: [],
    candidate: {
      experiences: [
        {
          company: "APTRANSCO",
          job_title: "AI Developer Intern",
          employment_type: null,
          location: null,
          start_date: { raw: "May 2024", value: "2024-05-01", precision: "month" },
          end_date: null,
          description: null,
          provenance: [
            { page: 1, quote: "AI Developer Intern, APTRANSCO", confidence: 0.8, verified: true },
          ],
        },
      ],
      education: [],
      skills: [
        {
          name: "Python",
          category: null,
          proficiency: null,
          years_of_experience: null,
          provenance: [{ page: 1, quote: "Python, Django", confidence: 0.5, verified: true }],
        },
      ],
      projects: [],
      certifications: [],
    },
    ...overrides,
  };
}

export function processingResult(overrides: Partial<ProcessingResult> = {}): ProcessingResult {
  return {
    document: resumeDocument(),
    run: run(),
    result: structuredResult(),
    ...overrides,
  };
}
