/**
 * From what the page said, and what the person changed, to the exact request.
 *
 * The review screen edits a `ReviewDraft` — plain text, as typed. `buildPayload`
 * is the only way a draft becomes a `JobIngestionRequest`, and the popup shows
 * its result verbatim, so what the person sees and what is sent cannot differ.
 *
 * The identity fields — `source_type`, `source_url`, `external_job_id` — are not
 * editable. They are what the API deduplicates on (see `_find_existing` in
 * services/api/app/services/job/service.py); letting them be edited would let a
 * typo save the same posting twice.
 *
 * Nothing is truncated. The API refuses an over-long field rather than cutting
 * it (a description cut at 50,000 characters silently loses its requirements
 * section), and so does this: the problem is shown, and Save waits until the
 * person has decided what to remove.
 */

import { FIELD_LIMITS, MAX_SOURCE_URL_LENGTH, type JobIngestionRequest } from "../contract";
import { cleanLabel, cleanMultiline } from "../extraction/text";
import type { ExtractedJob } from "../extraction/types";

/** The fields the person may edit, as the text currently in the form. */
export interface ReviewDraft {
  job_title: string;
  company_name: string;
  location_raw: string;
  description_text: string;
}

export type EditableField = keyof ReviewDraft;

export interface ReviewIssue {
  field: keyof JobIngestionRequest;
  severity: "error" | "warning";
  message: string;
}

export interface ReviewedPayload {
  payload: JobIngestionRequest;
  issues: ReviewIssue[];
  /** No errors: Save may be offered. Warnings do not block it. */
  sendable: boolean;
}

const LABELS: Record<keyof typeof FIELD_LIMITS, string> = {
  job_title: "job title",
  company_name: "company name",
  location_raw: "location",
  external_job_id: "job id",
  description_text: "description",
};

/**
 * Length as the API measures it. Pydantic's `max_length` counts code points;
 * `String.length` counts UTF-16 units, which would call an emoji two characters
 * and warn about a title the API would accept.
 */
export function codePointLength(value: string): number {
  return Array.from(value).length;
}

export function draftFrom(job: ExtractedJob): ReviewDraft {
  return {
    job_title: job.job_title,
    company_name: job.company_name ?? "",
    location_raw: job.location_raw ?? "",
    description_text: job.description_text,
  };
}

function checkLength(
  issues: ReviewIssue[],
  field: keyof typeof FIELD_LIMITS,
  value: string | null | undefined,
): void {
  if (!value) return;
  const length = codePointLength(value);
  const limit = FIELD_LIMITS[field];
  if (length > limit) {
    issues.push({
      field,
      severity: "error",
      message:
        `The ${LABELS[field]} is ${length.toLocaleString("en-US")} characters; EasyApply accepts at most ` +
        `${limit.toLocaleString("en-US")}. Nothing is cut automatically — shorten it before saving.`,
    });
  }
}

function checkSourceUrl(issues: ReviewIssue[], value: string): void {
  let url: URL | null = null;
  try {
    url = new URL(value);
  } catch {
    url = null;
  }
  if (!url || (url.protocol !== "https:" && url.protocol !== "http:") || !url.host) {
    issues.push({
      field: "source_url",
      severity: "error",
      message: "The page address is not an http(s) link, so EasyApply cannot save it.",
    });
  } else if (codePointLength(value) > MAX_SOURCE_URL_LENGTH) {
    issues.push({
      field: "source_url",
      severity: "error",
      message: `The page address is longer than ${MAX_SOURCE_URL_LENGTH} characters, which EasyApply does not accept.`,
    });
  }
}

export function buildPayload(job: ExtractedJob, draft: ReviewDraft): ReviewedPayload {
  // Key order is the order the review screen lists them in, and the order the
  // exact-request preview shows.
  const payload: JobIngestionRequest = {
    source_type: job.source_type,
    source_url: job.source_url,
    external_job_id: job.external_job_id,
    job_title: cleanLabel(draft.job_title),
    company_name: cleanLabel(draft.company_name) || null,
    location_raw: cleanLabel(draft.location_raw) || null,
    description_text: cleanMultiline(draft.description_text) || null,
  };

  const issues: ReviewIssue[] = [];

  if (!payload.job_title) {
    issues.push({ field: "job_title", severity: "error", message: "A job title is required." });
  }
  checkLength(issues, "job_title", payload.job_title);
  checkLength(issues, "company_name", payload.company_name);
  checkLength(issues, "location_raw", payload.location_raw);
  checkLength(issues, "external_job_id", payload.external_job_id);
  checkLength(issues, "description_text", payload.description_text);
  checkSourceUrl(issues, payload.source_url);

  if (!payload.description_text) {
    issues.push({
      field: "description_text",
      severity: "warning",
      message:
        "No description will be saved. EasyApply reads a job's requirements from its description, " +
        "so matching will have nothing to compare against.",
    });
  }

  return { payload, issues, sendable: !issues.some((issue) => issue.severity === "error") };
}

/** The request body, exactly as it goes on the wire. */
export function requestBody(payload: JobIngestionRequest): string {
  return JSON.stringify(payload);
}
