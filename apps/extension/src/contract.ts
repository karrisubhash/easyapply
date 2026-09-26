/**
 * The slice of the API contract this extension speaks.
 *
 * The types are the generated ones from @easyapply/shared, never a local copy:
 * a field renamed or retyped on `JobIngestionRequest` is a compile error here
 * rather than a 422 from a user's first capture. The numbers below are the one
 * thing a TypeScript type cannot carry, so test/contract.test.ts reads
 * packages/shared/openapi.json (and the API source, for the URL limit) and
 * fails if any of them disagrees with what the API enforces.
 *
 * The limits are used to warn in the review screen BEFORE sending. The API
 * stays the authority: anything it rejects anyway is shown as it said it.
 */

import type { ApiErrorBody, JobDetail, JobIngestionRequest } from "@easyapply/shared";

export type { ApiErrorBody, JobDetail, JobIngestionRequest };

export type JobSourceType = JobIngestionRequest["source_type"];

/** `maxLength` of each string field the extension sends, as the API publishes it. */
export const FIELD_LIMITS = {
  job_title: 500,
  company_name: 500,
  location_raw: 500,
  external_job_id: 255,
  description_text: 50_000,
} as const satisfies Partial<Record<keyof JobIngestionRequest, number>>;

/**
 * Not in the OpenAPI document: the API enforces it inside a validator
 * (`_MAX_URL_LENGTH` in services/api/app/schemas/job.py), which a JSON Schema
 * generated from the model cannot express.
 */
export const MAX_SOURCE_URL_LENGTH = 2048;

/** FastAPI's own 422 shape: one entry per field it refused. */
export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}
