import type {
  OperatorAttention,
  OperatorDocument,
  OperatorDocumentDetail,
  OperatorRun,
} from "@easyapply/shared";

/**
 * Operator fixtures.
 *
 * These mirror what the API actually returns, which notably excludes filenames,
 * page text, structured payloads and email addresses. A fixture that invented
 * those fields would let a UI test pass while rendering something the server
 * can never send.
 */

export function operatorDocument(
  overrides: Partial<OperatorDocument> = {},
): OperatorDocument {
  return {
    document_id: 1,
    user_id: 42,
    state: "exhausted",
    attempts_used: 3,
    max_attempts: 3,
    latest_run_id: 7,
    status: "failed",
    failed_stage: "structure",
    error_code: "ai_rate_limited",
    failure_classification: "transient",
    document_created_at: "2026-09-18T09:00:00Z",
    run_created_at: "2026-09-18T09:02:00Z",
    started_at: "2026-09-18T09:02:01Z",
    finished_at: "2026-09-18T09:02:09Z",
    next_retry_at: null,
    heartbeat_at: "2026-09-18T09:02:05Z",
    leased: false,
    ...overrides,
  };
}

export function attentionList(
  overrides: Partial<OperatorAttention> = {},
): OperatorAttention {
  return {
    documents: [operatorDocument()],
    counts: { exhausted: 1, completed: 4, waiting_retry: 2 },
    generated_at: "2026-09-18T09:10:00Z",
    ...overrides,
  };
}

export function operatorRun(overrides: Partial<OperatorRun> = {}): OperatorRun {
  return {
    id: 7,
    document_id: 1,
    attempt: 1,
    max_attempts: 3,
    status: "failed",
    failed_stage: "structure",
    error_code: "ai_rate_limited",
    failure_classification: "transient",
    page_count: 2,
    extracted_char_count: 512,
    ocr_page_count: 0,
    profile_version_id: null,
    created_at: "2026-09-18T09:00:10Z",
    started_at: "2026-09-18T09:00:11Z",
    finished_at: "2026-09-18T09:00:19Z",
    next_retry_at: null,
    heartbeat_at: "2026-09-18T09:00:15Z",
    leased: false,
    structurer: "provider",
    provider: "anthropic",
    model: "claude-opus-5",
    prompt_version: "2026-09-18.1",
    schema_version: "1",
    provider_metadata: { latency_ms: 812, input_tokens: 900 },
    ...overrides,
  };
}

export function operatorDocumentDetail(
  overrides: Partial<OperatorDocumentDetail> = {},
): OperatorDocumentDetail {
  return {
    document_id: 1,
    user_id: 42,
    content_type: "application/pdf",
    byte_size: 24_576,
    document_created_at: "2026-09-18T09:00:00Z",
    state: "exhausted",
    attempts_used: 2,
    max_attempts: 3,
    runs: [
      operatorRun({ id: 6, attempt: 1 }),
      operatorRun({
        id: 7,
        attempt: 2,
        error_code: "ai_timeout",
        next_retry_at: "2026-09-18T09:05:00Z",
      }),
    ],
    ...overrides,
  };
}
