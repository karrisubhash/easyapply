/**
 * The HTTP edge.
 *
 * Everything the UI knows about failure is an ApiError carrying the status and
 * the backend's own `code`. The frontend never decides what is valid — it
 * renders what the API said.
 */

import type { ApiErrorBody } from "@easyapply/shared";

import { sessionTagHeaders } from "@/lib/session";

/** Field-level problems, as FastAPI reports them for a 422 shape error. */
export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly issues: ValidationIssue[];

  constructor(status: number, code: string, message: string, issues: ValidationIssue[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.issues = issues;
  }

  /** The resource is not there — usually an empty state, not a failure. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The request was refused by the state of the resource. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /**
   * No session, or not the session this page was rendered for. Never cured by
   * asking again: the query cache's handler deals with it (src/app/providers.tsx).
   */
  get isSessionFailure(): boolean {
    return this.status === 401 || this.code === "session_changed";
  }

  /** The submitted data was rejected, by Pydantic or by a database CHECK. */
  get isValidation(): boolean {
    return this.status === 422;
  }
}

function isValidationIssueArray(detail: unknown): detail is ValidationIssue[] {
  return Array.isArray(detail) && detail.every((item) => typeof item === "object" && item !== null);
}

/**
 * Not every failure produces the API's error body. A crash in the proxy route,
 * or anything that answers before it, replies with HTML — and letting JSON.parse
 * throw on that surfaces "Unexpected token <" to the user instead of an ApiError
 * the UI knows how to render.
 */
export function parseBody(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * THE way this app reaches the API proxy. Every call — JSON, a file upload, a
 * download — goes through here, so every one carries the account tag the page
 * was rendered for (see src/lib/session.ts). A test fails if anything under
 * src/ fetches /api/backend any other way.
 */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`/api/backend/${path}`, {
    ...init,
    headers: { ...sessionTagHeaders(), ...(init?.headers as Record<string, string> | undefined) },
  });
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await requestWithStatus<T>(path, init)).data;
}

/**
 * `request`, keeping the status code of a success.
 *
 * For the endpoints where 200 and 201 are different answers — an idempotent
 * create returns 200 with the row that ALREADY existed, unchanged — and the UI
 * has to say which one happened.
 */
export async function requestWithStatus<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; status: number }> {
  // FormData carries its own multipart Content-Type, complete with the boundary
  // the server needs to find any part at all; declaring JSON over it is how an
  // upload silently arrives empty.
  const sendsJson = init?.body !== undefined && typeof init.body === "string";

  const response = await apiFetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(sendsJson ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  const payload: unknown = parseBody(await response.text());

  if (response.ok) {
    return { data: payload as T, status: response.status };
  }

  // FastAPI's own 422 carries a list of field issues; ours carries {detail, code}.
  const body = payload as Partial<ApiErrorBody> & { detail?: unknown };

  if (isValidationIssueArray(body?.detail)) {
    return Promise.reject(
      new ApiError(response.status, "request_validation", "Some fields need attention.", body.detail),
    );
  }

  const detail = typeof body?.detail === "string" ? body.detail : "Something went wrong.";
  throw new ApiError(response.status, body?.code ?? "unknown", detail);
}
