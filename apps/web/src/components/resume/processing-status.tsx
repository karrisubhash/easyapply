import type { ProcessingRun } from "@easyapply/shared";

import { Badge } from "@/components/ui/badge";
import { humanise } from "@/lib/utils";

/**
 * What each stage means to someone watching it happen.
 *
 * Processing is queued and then worked on by something other than the request
 * that started it, so "Queued" is now a state a user genuinely sits in rather
 * than a millisecond nobody sees.
 */
const STAGE_LABELS: Record<string, string> = {
  uploaded: "Queued",
  validating: "Checking the file",
  extracting: "Reading the text",
  ocr_required: "Needs scanning",
  ocr_processing: "Scanning the pages",
  structuring: "Interpreting the content",
  validating_result: "Checking the result",
  materialising: "Building your draft",
  completed: "Ready to review",
  failed: "Failed",
};

/**
 * Failures the user can do something about get a sentence that says what.
 *
 * Every string here is written for the person reading it. The backend's own
 * message is the fallback, and it is written from a fixed vocabulary for the
 * same reason — no exception text, no file paths, no library prose, because all
 * three quote the document and the document is the user's personal data.
 */
const FAILURE_HELP: Record<string, string> = {
  // --- the document ---
  ocr_unavailable:
    "This looks like a scanned document, and text recognition is not available in this environment.",
  no_text_extracted: "No readable text could be found in this document.",
  malformed_pdf: "The PDF could not be read. It may be damaged.",
  malformed_docx: "The DOCX could not be read. It may be damaged.",
  document_encrypted: "This PDF is password protected, so its text cannot be read.",
  unsupported_file_type: "Only PDF and DOCX resumes can be read.",
  // --- around the document ---
  stored_object_missing: "The stored file could not be read back. Trying again may help.",
  worker_abandoned:
    "Processing was interrupted before it finished. Trying again usually resolves it.",
  unexpected_error: "Processing stopped unexpectedly. Trying again may help.",
  // --- the interpretation service ---
  ai_provider_unavailable: "The interpretation service could not be reached. Try again.",
  ai_timeout: "The interpretation service took too long to answer. Try again.",
  ai_rate_limited: "The interpretation service is busy. Try again in a moment.",
  ai_provider_error: "The interpretation service failed to answer. Try again.",
  ai_output_malformed: "The interpretation was incomplete. Trying again usually resolves it.",
  ai_output_invalid: "The interpretation did not fit the profile. This needs looking at.",
  ai_content_rejected: "The interpretation service declined to process this document.",
  // --- our configuration, not their day ---
  ai_not_configured: "AI interpretation is switched on but not configured.",
  ai_unauthorized: "AI interpretation is not correctly credentialed.",
};

export function statusLabel(status: string): string {
  return STAGE_LABELS[status] ?? humanise(status);
}

/** Whether the server still intends to do more work on this attempt. */
export function isQueued(run: ProcessingRun | null | undefined): boolean {
  return run?.status === "uploaded";
}

/**
 * Whether this attempt is deliberately waiting before it starts.
 *
 * Derived on the server — a client that worked it out from `next_retry_at`
 * would be a second copy of the rule. It matters because a backoff and a queue
 * are the same `status`, and a user told "Queued" for two minutes reasonably
 * concludes something is broken.
 */
export function isWaitingToRetry(run: ProcessingRun | null | undefined): boolean {
  return run?.is_waiting_to_retry === true;
}

export function ProcessingStatusBadge({ run }: { run: ProcessingRun | null | undefined }) {
  if (!run) {
    return <Badge variant="secondary">Not processed</Badge>;
  }
  if (run.status === "completed") {
    return <Badge variant="confirmed">Ready to review</Badge>;
  }
  if (run.status === "failed") {
    return <Badge variant="outline" className="border-destructive text-destructive">Failed</Badge>;
  }
  if (isWaitingToRetry(run)) {
    return <Badge variant="secondary">Waiting to retry</Badge>;
  }
  if (isQueued(run)) {
    return <Badge variant="secondary">Queued</Badge>;
  }
  return <Badge variant="draft">{statusLabel(run.status)}</Badge>;
}


/**
 * The pipeline stage a failure happened in, as a phrase that completes
 * "failed while …".
 *
 * `failed_stage` is a column value — `validate_result`, `materialise` — and it
 * was printed as-is into "failed at the validate_result stage", or as "at the
 * stage" when the run had none. An unknown value gets no phrase at all rather
 * than the raw word: the title still says it failed, which is the fact.
 */
const FAILED_WHILE: Record<string, string> = {
  validate: "checking the file",
  extract: "reading the text",
  ocr: "scanning the pages",
  structure: "interpreting the content",
  validate_result: "checking the result",
  materialise: "building your draft",
};

/** "Processing failed while scanning the pages", or just "Processing failed". */
export function failureTitle(run: Pick<ProcessingRun, "failed_stage">): string {
  const phrase = run.failed_stage ? FAILED_WHILE[run.failed_stage] : undefined;
  return phrase ? `Processing failed while ${phrase}` : "Processing failed";
}

export function failureExplanation(run: ProcessingRun): string {
  if (run.error_code && FAILURE_HELP[run.error_code]) {
    return FAILURE_HELP[run.error_code];
  }
  return run.error_message ?? "Processing failed.";
}
