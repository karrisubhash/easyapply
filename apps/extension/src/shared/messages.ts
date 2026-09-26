/**
 * The messages the three parts of the extension exchange.
 *
 *   popup ──extract──▶ content script   (chrome.tabs.sendMessage, one tab)
 *   popup ──detect-form──▶ content script   (the application form's fields; no values)
 *   popup ──fill-form────▶ content script   (the values the person approved; text fields only)
 *   popup ──submit───▶ background       (chrome.runtime.sendMessage)
 *   popup ──last─────▶ background
 *   popup ──session──▶ background       (who the web app says this browser is)
 *   popup ──api──────▶ background       (one of the calls in src/api/calls.ts)
 *
 * The content script only reads the page and answers. It never talks to the
 * network and never asks for a save: the page it lives in is third-party
 * content, and the background refuses every request from anything but the
 * popup.
 */

import type { ApiCall } from "../api/calls";
import { isApiCall } from "../api/calls";
import { isSessionTag } from "../api/session";
import type { JobIngestionRequest } from "../contract";
import type { FillInstruction } from "../forms/types";

export const EXTRACT = "easyapply/extract";
export const SUBMIT = "easyapply/submit";
export const LAST_SUBMISSION = "easyapply/last-submission";
export const SESSION = "easyapply/session";
export const API = "easyapply/api";
export const DETECT_FORM = "easyapply/detect-form";
export const FILL_FORM = "easyapply/fill-form";

/** Bounds on a fill request, so a malformed one is refused rather than walked. */
export const FILL_LIMITS = { fields: 200, key: 200, label: 2_000, value: 10_000 } as const;

export interface ExtractRequest {
  type: typeof EXTRACT;
}

export interface DetectFormRequest {
  type: typeof DETECT_FORM;
}

export interface FillFormRequest {
  type: typeof FILL_FORM;
  instructions: FillInstruction[];
}

export type ContentRequest = ExtractRequest | DetectFormRequest | FillFormRequest;

export interface SubmitRequest {
  type: typeof SUBMIT;
  tabId: number;
  payload: JobIngestionRequest;
  /**
   * What the review screen called the account when Save was pressed, or null
   * if it could not tell. Display only — NOT identity: the web app decides
   * that from the session.
   */
  account: string | null;
  /**
   * The tag of the account the review screen named (src/api/session.ts), or
   * null when the web app gave none. Sent with the save, so the proxy refuses
   * it if the browser is now signed in to someone else; and kept with what the
   * worker remembers, so a later account in the same browser is not told its
   * job is already saved.
   */
  sessionTag: string | null;
}

export interface LastSubmissionRequest {
  type: typeof LAST_SUBMISSION;
  tabId: number;
}

export interface SessionRequest {
  type: typeof SESSION;
}

/** One read or write of the backend, as data. The worker builds the request from it. */
export interface ApiRequestMessage {
  type: typeof API;
  call: ApiCall;
  /** The tag of the account the popup is showing, sent with the request; null when unknown. */
  sessionTag: string | null;
}

export type BackgroundRequest = SubmitRequest | LastSubmissionRequest | SessionRequest | ApiRequestMessage;

/**
 * A save that succeeded, remembered for the tab it was made from.
 *
 * Kept in `chrome.storage.session`, so that reopening the popup on the same
 * posting says "saved" instead of offering the button again — the service
 * worker itself may have been stopped in between. Only what the popup shows is
 * kept: not the description.
 */
export interface SubmissionRecord {
  tabId: number;
  /** Whose save this was, as the review screen showed it. Display only. */
  account: string | null;
  /**
   * The tag of the account the save was made for. What "the same account"
   * means when the popup is reopened: never the label, which two accounts can
   * share. Null: unknown, and so never the same as anyone.
   */
  sessionTag: string | null;
  sourceUrl: string;
  externalJobId: string | null;
  outcome: "created" | "existing";
  jobId: number;
  jobTitle: string;
  /** When EasyApply first saved the job (its `created_at`). */
  savedAt: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isExtractRequest(value: unknown): value is ExtractRequest {
  return isObject(value) && value.type === EXTRACT;
}

function isBoundedString(value: unknown, limit: number): value is string {
  return typeof value === "string" && value.length <= limit;
}

export function isContentRequest(value: unknown): value is ContentRequest {
  if (!isObject(value)) return false;
  if (value.type === EXTRACT || value.type === DETECT_FORM) return true;
  if (value.type !== FILL_FORM || !Array.isArray(value.instructions)) return false;
  return (
    value.instructions.length <= FILL_LIMITS.fields &&
    value.instructions.every(
      (item: unknown) =>
        isObject(item) &&
        isBoundedString(item.key, FILL_LIMITS.key) &&
        isBoundedString(item.label, FILL_LIMITS.label) &&
        isBoundedString(item.value, FILL_LIMITS.value),
    )
  );
}

/** A session tag as the worker accepts one from the popup: a well-formed tag, or null for none. */
function isTagOrNull(value: unknown): value is string | null {
  return value === null || isSessionTag(value);
}

export function isBackgroundRequest(value: unknown): value is BackgroundRequest {
  if (!isObject(value)) return false;
  if (value.type === SESSION) return true;
  if (value.type === API) return isApiCall(value.call) && isTagOrNull(value.sessionTag);
  if (typeof value.tabId !== "number") return false;
  if (value.type === LAST_SUBMISSION) return true;
  return value.type === SUBMIT && isObject(value.payload) && isTagOrNull(value.sessionTag);
}
