/**
 * Everything the popup needs from Chrome, behind one interface.
 *
 * The popup's logic (app.ts) sees only `PopupPort`, so the tests can drive it
 * against the real content-script and background handlers without a browser.
 */

import type { ApiCall, ResultOf } from "../api/calls";
import type { ApiFailure, ApiResult } from "../api/client";
import { isSubmitOutcome, type SubmitOutcome } from "../api/jobs";
import { isSessionState, type SessionState } from "../api/session";
import type { JobIngestionRequest } from "../contract";
import type { ExtractionResult } from "../extraction/types";
import type { FillInstruction, FillReport, FormDetection } from "../forms/types";
import { API, DETECT_FORM, EXTRACT, FILL_FORM, LAST_SUBMISSION, SESSION, SUBMIT, type SubmissionRecord } from "../shared/messages";

export interface TargetTab {
  id: number;
  /** Null when Chrome does not show it to this extension (a site it has no permission for). */
  url: string | null;
}

export interface PopupPort {
  targetTab(): Promise<TargetTab | null>;
  /** The page's reading, or null if no content script answered in that tab. */
  extract(tabId: number): Promise<ExtractionResult | null>;
  /**
   * Save, for the account the review screen names: `account` is its label
   * (display only), `sessionTag` its tag (sent with the save; null if unknown).
   */
  submit(tabId: number, payload: JobIngestionRequest, account: string | null, sessionTag: string | null): Promise<SubmitOutcome>;
  lastSubmission(tabId: number): Promise<SubmissionRecord | null>;
  /** Who the web app says this browser is signed in as. */
  session(): Promise<SessionState>;
  /** One backend call, made by the service worker (src/api/calls.ts), for the account with this tag. */
  api<C extends ApiCall>(call: C, sessionTag: string | null): Promise<ApiResult<ResultOf<C>>>;
  /** The application form on the page: its fields, never their values. Null if no content script answered. */
  detectForm(tabId: number): Promise<FormDetection | null>;
  /** Fill the approved values. Null if no content script answered. */
  fillForm(tabId: number, instructions: FillInstruction[]): Promise<FillReport | null>;
}

function isFormDetection(value: unknown): value is FormDetection {
  if (typeof value !== "object" || value === null) return false;
  const detection = value as { status?: unknown; fields?: unknown; message?: unknown };
  if (detection.status === "detected") return Array.isArray(detection.fields);
  return (detection.status === "no_form" || detection.status === "unsupported") && typeof detection.message === "string";
}

function isFillReport(value: unknown): value is FillReport {
  if (typeof value !== "object" || value === null) return false;
  const report = value as { filled?: unknown; skipped?: unknown };
  return Array.isArray(report.filled) && Array.isArray(report.skipped);
}

const FAILURE_KINDS = new Set([
  "session_required",
  "session_changed",
  "not_found",
  "conflict",
  "refused",
  "api_unreachable",
  "network",
  "timeout",
  "unexpected_response",
]);

/** Whether a worker's answer is an `ApiResult` at all. The data itself was shape-checked in the worker. */
export function isApiResult(value: unknown): value is ApiResult<unknown> {
  if (typeof value !== "object" || value === null) return false;
  const result = value as { ok?: unknown; status?: unknown; kind?: unknown; message?: unknown };
  if (result.ok === true) return typeof result.status === "number" && "data" in result;
  return result.ok === false && FAILURE_KINDS.has(result.kind as string) && typeof result.message === "string";
}

const NO_WORKER: ApiFailure = {
  ok: false,
  kind: "network",
  status: null,
  code: null,
  message: "The extension's background worker did not answer.",
};

/**
 * Which tab to read.
 *
 * Opened from the toolbar, the popup reads the active tab of its window. A
 * `?tabId=` names one explicitly: that is how the browser test opens the review
 * screen as a page of its own for a given posting, since a test cannot click
 * the toolbar. It grants nothing — the popup page is not web-accessible, so no
 * website can open it, and the tab it names is still only read.
 */
export function chromePort(search: string): PopupPort {
  return {
    async targetTab() {
      const requested = Number(new URLSearchParams(search).get("tabId"));
      if (Number.isInteger(requested) && requested > 0) {
        const tab = await chrome.tabs.get(requested).catch(() => null);
        return tab?.id === undefined ? null : { id: tab.id, url: tab.url ?? null };
      }
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      return tab?.id === undefined ? null : { id: tab.id, url: tab.url ?? null };
    },

    async extract(tabId) {
      try {
        const answer: unknown = await chrome.tabs.sendMessage(tabId, { type: EXTRACT }, { frameId: 0 });
        return (answer as ExtractionResult | undefined) ?? null;
      } catch {
        // "Receiving end does not exist": no content script in that tab.
        return null;
      }
    },

    async submit(tabId, payload, account, sessionTag) {
      const answer: unknown = await chrome.runtime.sendMessage({ type: SUBMIT, tabId, payload, account, sessionTag });
      // Chrome resolves with undefined when no listener answered.
      return isSubmitOutcome(answer)
        ? answer
        : { kind: "unreachable", code: "network", message: "The extension's background worker did not answer." };
    },

    async lastSubmission(tabId) {
      return ((await chrome.runtime.sendMessage({ type: LAST_SUBMISSION, tabId })) as SubmissionRecord | null) ?? null;
    },

    async session() {
      const answer: unknown = await chrome.runtime.sendMessage({ type: SESSION }).catch(() => null);
      return isSessionState(answer)
        ? answer
        : { kind: "unknown", message: "The extension's background worker did not answer." };
    },

    async detectForm(tabId) {
      try {
        const answer: unknown = await chrome.tabs.sendMessage(tabId, { type: DETECT_FORM }, { frameId: 0 });
        return isFormDetection(answer) ? answer : null;
      } catch {
        return null;
      }
    },

    async fillForm(tabId, instructions) {
      try {
        const answer: unknown = await chrome.tabs.sendMessage(tabId, { type: FILL_FORM, instructions }, { frameId: 0 });
        return isFillReport(answer) ? answer : null;
      } catch {
        return null;
      }
    },

    async api<C extends ApiCall>(call: C, sessionTag: string | null): Promise<ApiResult<ResultOf<C>>> {
      const answer: unknown = await chrome.runtime.sendMessage({ type: API, call, sessionTag }).catch(() => null);
      return isApiResult(answer) ? (answer as ApiResult<ResultOf<C>>) : NO_WORKER;
    },
  };
}
