/**
 * The service worker's job: send reviewed jobs, one at a time per posting.
 *
 * Why the save happens here rather than in the popup. A popup is destroyed the
 * moment it loses focus — clicking back into the page mid-save would abort the
 * request, and the person would be left not knowing whether it landed. The
 * worker finishes the request regardless, remembers the result for that tab,
 * and the popup reopened a second later shows it.
 *
 * Duplicate saves are stopped at two levels. The API is the real guard: the
 * same posting answers 200 with the job it already has. This worker only makes
 * sure the second click, or the popup reopened during a slow save, joins the
 * request already in flight instead of sending another.
 *
 * Everything after the save goes through here too — requirements, profile
 * versions, matches, tailoring — as one of the calls in src/api/calls.ts. The
 * worker checks the call's shape, builds the request itself, joins an
 * identical call already in flight, and records a diagnostic line for it (no
 * bodies, no personal data: ./diagnostics.ts). Only the popup may ask.
 *
 * Every message that makes a request carries the tag of the account the popup
 * is showing (src/api/session.ts), and the request is sent with it. Joining is
 * per account: a call or save in flight for one account is never handed to a
 * popup showing another, and one for an account with no known tag is never
 * joined at all.
 */

import { callKey, prepare, type PreparedCall } from "../api/calls";
import type { ApiResult } from "../api/client";
import type { JobIngestionRequest } from "../contract";
import type { SubmitOutcome } from "../api/jobs";
import { isSessionTag, type SessionState } from "../api/session";
import {
  API,
  isBackgroundRequest,
  LAST_SUBMISSION,
  SESSION,
  type BackgroundRequest,
  type SubmissionRecord,
} from "../shared/messages";
import { createDiagnostics, type Diagnostics } from "./diagnostics";

/** The part of chrome.storage.session this worker uses. */
export interface SessionStore {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
  remove(key: string): Promise<void>;
}

export interface MessageSender {
  id?: string;
  url?: string;
}

export interface BackgroundDeps {
  /** Save a job, for the account with this tag (src/api/jobs.ts `submitJob`). */
  submit(payload: JobIngestionRequest, sessionTag: string | null): Promise<SubmitOutcome>;
  /** Ask the web app who this browser is signed in as. */
  session(): Promise<SessionState>;
  /** Perform one prepared backend request for the account with this tag (src/api/client.ts `apiRequest`). */
  request(prepared: PreparedCall<unknown>, sessionTag: string | null): Promise<ApiResult<unknown>>;
  store: SessionStore;
  /** This extension's own id: `chrome.runtime.id`. */
  extensionId: string;
  diagnostics?: Diagnostics;
  /** Milliseconds, for timing requests. */
  clock?: () => number;
}

export function submissionKey(tabId: number): string {
  return `submission:${tabId}`;
}

/** Which posting a payload is, in the API's own terms (tier 1, else tier 2). */
function identityOf(payload: JobIngestionRequest): string {
  return payload.external_job_id
    ? `${payload.source_type}#id:${payload.external_job_id}`
    : `${payload.source_type}#url:${payload.source_url}`;
}

function isSubmissionRecord(value: unknown): value is SubmissionRecord {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Partial<SubmissionRecord>;
  return typeof record.jobId === "number" && typeof record.sourceUrl === "string";
}

/** A remembered save as the popup may use it: one without a well-formed tag is an unknown account's. */
function normalizedRecord(record: SubmissionRecord): SubmissionRecord {
  return isSessionTag(record.sessionTag) ? record : { ...record, sessionTag: null };
}

export function createBackground({
  submit,
  session,
  request,
  store,
  extensionId,
  diagnostics = createDiagnostics(),
  clock = () => Date.now(),
}: BackgroundDeps) {
  const inFlight = new Map<string, Promise<SubmitOutcome>>();
  const callsInFlight = new Map<string, Promise<ApiResult<unknown>>>();

  /**
   * Only the popup may ask for a save.
   *
   * Content scripts can message this worker too, and they live inside pages
   * written by whoever wrote the job posting. A save is something the person
   * decides on the review screen, so a request that did not come from that
   * screen is refused however well-formed it is.
   */
  function fromPopup(sender: MessageSender): boolean {
    return sender.id === extensionId && (sender.url ?? "").startsWith(`chrome-extension://${extensionId}/popup.html`);
  }

  async function remember(
    tabId: number,
    account: string | null,
    sessionTag: string | null,
    payload: JobIngestionRequest,
    outcome: SubmitOutcome,
  ): Promise<void> {
    if (outcome.kind !== "created" && outcome.kind !== "existing") return;
    const record: SubmissionRecord = {
      tabId,
      account,
      sessionTag,
      sourceUrl: payload.source_url,
      externalJobId: payload.external_job_id ?? null,
      outcome: outcome.kind,
      jobId: outcome.job.id,
      jobTitle: outcome.job.job_title,
      savedAt: outcome.job.created_at,
    };
    await store.set(submissionKey(tabId), record);
  }

  function send(
    tabId: number,
    account: string | null,
    sessionTag: string | null,
    payload: JobIngestionRequest,
  ): Promise<SubmitOutcome> {
    // The same posting, for the same account. A save for an unknown account
    // (no tag) joins no other save, and no other save joins it.
    const key = sessionTag === null ? null : `${sessionTag} ${identityOf(payload)}`;
    const pending = key === null ? undefined : inFlight.get(key);
    if (pending) return pending;

    const started = clock();
    const saving = submit(payload, sessionTag)
      .then(async (outcome) => {
        diagnostics.record({
          route: "POST /jobs",
          outcome: outcome.kind,
          status: outcome.kind === "rejected" ? outcome.status : outcome.kind === "created" ? 201 : outcome.kind === "existing" ? 200 : null,
          ms: clock() - started,
          ids: outcome.kind === "created" || outcome.kind === "existing" ? { jobId: outcome.job.id } : {},
        });
        // A storage failure must not turn a successful save into a failure.
        await remember(tabId, account, sessionTag, payload, outcome).catch(() => undefined);
        return outcome;
      })
      .finally(() => {
        if (key !== null) inFlight.delete(key);
      });
    if (key !== null) inFlight.set(key, saving);
    return saving;
  }

  /** One backend call, joined to an identical one for the same account already in flight. */
  function call(prepared: PreparedCall<unknown>, sessionTag: string | null, key: string | null): Promise<ApiResult<unknown>> {
    const pending = key === null ? undefined : callsInFlight.get(key);
    if (pending) return pending;
    const started = clock();
    const running = request(prepared, sessionTag)
      .then((result) => {
        diagnostics.record({
          route: prepared.route,
          outcome: result.ok ? "ok" : result.kind,
          status: result.status,
          ms: clock() - started,
          ids: prepared.ids,
        });
        return result;
      })
      .finally(() => {
        if (key !== null) callsInFlight.delete(key);
      });
    if (key !== null) callsInFlight.set(key, running);
    return running;
  }

  async function lastSubmission(tabId: number): Promise<SubmissionRecord | null> {
    const value = await store.get(submissionKey(tabId)).catch(() => null);
    return isSubmissionRecord(value) ? normalizedRecord(value) : null;
  }

  return {
    /**
     * Answer a message, or return null if it is not one this worker handles
     * (or not from someone allowed to send it).
     */
    handle(
      message: unknown,
      sender: MessageSender,
    ): Promise<SubmitOutcome | SubmissionRecord | SessionState | ApiResult<unknown> | null> | null {
      if (!isBackgroundRequest(message) || !fromPopup(sender)) return null;
      const asked: BackgroundRequest = message;
      if (asked.type === SESSION) return session();
      if (asked.type === API) return call(prepare(asked.call), asked.sessionTag, callKey(asked.call, asked.sessionTag));
      if (asked.type === LAST_SUBMISSION) return lastSubmission(asked.tabId);
      return send(asked.tabId, typeof asked.account === "string" ? asked.account : null, asked.sessionTag, asked.payload);
    },

    /** What this worker has recorded, for tests and debugging. */
    diagnostics(): Diagnostics {
      return diagnostics;
    },

    /** A tab closed: what it saved is no longer anything to show. */
    forgetTab(tabId: number): Promise<void> {
      return store.remove(submissionKey(tabId)).catch(() => undefined);
    },
  };
}

export type Background = ReturnType<typeof createBackground>;
