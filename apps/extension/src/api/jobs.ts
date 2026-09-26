/**
 * Saving a job: one POST, and every way it can come back.
 *
 * The request goes to the EasyApply web app's own proxy (`/api/backend/jobs`),
 * not to the API directly, for the reason the proxy exists at all: identity is
 * decided and attached THERE, server-side (apps/web/src/server/auth/identity.ts).
 * This extension sends no identity of its own — no user id, no email, no
 * development header, no token. It sends the browser's session cookie for the
 * web app (`credentials: "include"`), exactly as the web UI's own requests do,
 * without ever reading it (HttpOnly; no `cookies` permission). The job is saved
 * for whoever that session is — the account the web app shows in this browser,
 * which the review screen names before anything is sent (see ./session.ts).
 * Like every backend request it goes through `fetchBackend` (./client.ts), so
 * it carries the tag of the account the review screen named: if the browser
 * has since been signed in to a different account, the proxy refuses it (409
 * `session_changed`) rather than saving into the wrong one.
 *
 * Duplicates are the API's to prevent, and it does: the same posting (same
 * source, same external id) answers 200 with the job it already had, UNCHANGED,
 * instead of 201 with a new one. Both are reported, distinctly.
 */

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE } from "@easyapply/shared";

import type { ApiErrorBody, JobDetail, JobIngestionRequest, ValidationIssue } from "../contract";
import { requestBody } from "../review/payload";
import { backendUrl, fetchBackend } from "./client";

export type UnreachableCode = "network" | "timeout" | "api_unreachable" | "unexpected_response";

export type SubmitOutcome =
  /** 201: a new job, structured in the same request. */
  | { kind: "created"; job: JobDetail }
  /** 200: this posting was already saved; `job` is that row, as it was. */
  | { kind: "existing"; job: JobDetail }
  /** The server answered and refused. `issues` is filled for a field-level 422. */
  | { kind: "rejected"; status: number; code: string; message: string; issues: ValidationIssue[] }
  /** No usable answer at all. */
  | { kind: "unreachable"; code: UnreachableCode; message: string };

export interface SubmitOptions {
  /** The web app's origin, e.g. http://localhost:3000. */
  webUrl: string;
  /** The account tag the save is for (./session.ts); null when the web app gave none. */
  sessionTag: string | null;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

export const DEFAULT_TIMEOUT_MS = 30_000;

const OUTCOME_KINDS = new Set(["created", "existing", "rejected", "unreachable"]);

/** Whether a message answer is a `SubmitOutcome` at all — for the popup, which receives it from another context. */
export function isSubmitOutcome(value: unknown): value is SubmitOutcome {
  if (typeof value !== "object" || value === null) return false;
  const outcome = value as { kind?: unknown; job?: unknown; message?: unknown };
  if (!OUTCOME_KINDS.has(outcome.kind as string)) return false;
  return outcome.kind === "created" || outcome.kind === "existing"
    ? typeof outcome.job === "object" && outcome.job !== null
    : typeof outcome.message === "string";
}

export function jobsEndpoint(webUrl: string): string {
  return backendUrl(webUrl, "/jobs");
}

export function jobPageUrl(webUrl: string, jobId: number): string {
  return new URL(`/jobs/${jobId}`, webUrl).href;
}

function parseBody(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** Enough of `JobDetail` to trust that the answer came from EasyApply at all. */
function isJobDetail(value: unknown): value is JobDetail {
  if (typeof value !== "object" || value === null) return false;
  const job = value as Partial<JobDetail>;
  return typeof job.id === "number" && typeof job.job_title === "string" && typeof job.created_at === "string";
}

function isValidationIssues(value: unknown): value is ValidationIssue[] {
  return (
    Array.isArray(value) &&
    value.every((item) => typeof item === "object" && item !== null && typeof item.msg === "string")
  );
}

export async function submitJob(
  payload: JobIngestionRequest,
  { webUrl, sessionTag, fetch: fetchImpl = fetch, timeoutMs = DEFAULT_TIMEOUT_MS }: SubmitOptions,
): Promise<SubmitOutcome> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  let response: Response;
  let text: string;
  try {
    response = await fetchBackend(
      fetchImpl,
      jobsEndpoint(webUrl),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: requestBody(payload),
        // The browser's session for the web app, as the web UI presents it. The
        // web app decides who that is; this extension neither reads nor holds it.
        credentials: "include",
        cache: "no-store",
        // A redirect would be somebody else answering; do not follow it with a
        // job posting in hand.
        redirect: "error",
        signal: controller.signal,
      },
      sessionTag,
    );
    text = await response.text();
  } catch {
    return timedOut
      ? {
          kind: "unreachable",
          code: "timeout",
          message:
            `EasyApply did not answer within ${Math.round(timeoutMs / 1000)} seconds. The job may still have ` +
            "been saved — saving again is safe, because EasyApply recognises a posting it already has.",
        }
      : { kind: "unreachable", code: "network", message: `Could not reach EasyApply at ${webUrl}.` };
  } finally {
    clearTimeout(timer);
  }

  const body = parseBody(text);

  if (response.status === 201 || response.status === 200) {
    if (!isJobDetail(body)) {
      return {
        kind: "unreachable",
        code: "unexpected_response",
        message: `Something at ${webUrl} answered, but not the way EasyApply does.`,
      };
    }
    return { kind: response.status === 201 ? "created" : "existing", job: body };
  }

  const error = (typeof body === "object" && body !== null ? body : {}) as Partial<ApiErrorBody> & {
    detail?: unknown;
  };

  // The proxy's own answer when the API behind it is down.
  if (response.status === 502 && error.code === "api_unreachable") {
    return {
      kind: "unreachable",
      code: "api_unreachable",
      message: typeof error.detail === "string" ? error.detail : "The EasyApply API is unreachable.",
    };
  }

  // The session answers, whether or not they came with a sentence: the popup
  // says its own words for both, and must be able to tell which one it was.
  if (response.status === 401 && error.code === SESSION_REQUIRED_CODE) {
    return {
      kind: "rejected",
      status: 401,
      code: SESSION_REQUIRED_CODE,
      message: typeof error.detail === "string" ? error.detail : "You are not signed in to EasyApply.",
      issues: [],
    };
  }
  if (response.status === 409 && error.code === SESSION_CHANGED_CODE) {
    return {
      kind: "rejected",
      status: 409,
      code: SESSION_CHANGED_CODE,
      message:
        typeof error.detail === "string" ? error.detail : "This browser is signed in to EasyApply as a different account now.",
      issues: [],
    };
  }

  if (isValidationIssues(error.detail)) {
    return {
      kind: "rejected",
      status: response.status,
      code: "request_validation",
      message: "EasyApply refused some of the fields.",
      issues: error.detail,
    };
  }

  if (typeof error.detail === "string") {
    return {
      kind: "rejected",
      status: response.status,
      code: typeof error.code === "string" ? error.code : "unknown",
      message: error.detail,
      issues: [],
    };
  }

  if (response.ok) {
    return {
      kind: "unreachable",
      code: "unexpected_response",
      message: `Something at ${webUrl} answered ${response.status}, which EasyApply never does for a save.`,
    };
  }

  return {
    kind: "rejected",
    status: response.status,
    code: "unexpected_response",
    message: `EasyApply answered ${response.status} without saying why.`,
    issues: [],
  };
}
