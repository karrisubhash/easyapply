/**
 * The one transport for everything the extension asks EasyApply after a save:
 * requirements, profile versions, matches, tailored resumes.
 *
 * Same road as the save (./jobs.ts), for the same reason: the web app's proxy
 * (`/api/backend/*`) is where identity is decided and attached, server-side
 * (apps/web/src/server/auth/identity.ts). Requests carry the browser's session
 * cookie for the web app (`credentials: "include"`) and nothing else that could
 * say who this is — no user id, no email, no token, in any body or query
 * string. Whose data comes back is the web app's decision, and the API scopes
 * every read to that person: another account's id is a 404.
 *
 * The one header they add is the account tag the web app itself reported
 * (`X-EasyApply-Session`, ./session.ts), when it reported one. It proves
 * nothing and grants nothing; it lets the proxy refuse a request made for an
 * account this browser is no longer signed in to (409 `session_changed`), so
 * a popup opened for one person can never act for whoever signed in since.
 * Every request to /api/backend goes through `fetchBackend` below, which is
 * what attaches it (test/backend-fetch.test.ts holds src/ to that).
 *
 * Every way a request can end is a value, never an exception, because the
 * popup must say something specific in each case. The categories are the ones
 * a person can act on differently:
 *
 *   session_required   the proxy's 401: sign in, then try again.
 *   session_changed    the proxy's 409: this browser is signed in to a
 *                      different account than the one the request was for.
 *   not_found          404: not in this account (or gone).
 *   conflict           409: the resource's state refuses — not structured yet,
 *                      a draft profile version. The API uses one code for
 *                      several reasons, so callers find out which by READING
 *                      state first, never by parsing this message.
 *   refused            any other answer with a reason (403, 422, the API's own
 *                      401 `unauthenticated`, 500 `auth_misconfigured`).
 *   api_unreachable    the proxy's 502: the web app is up, the API is not.
 *   network / timeout  nothing answered, or not in time.
 *   unexpected_response something answered, but not the way EasyApply does.
 */

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE, SESSION_TAG_HEADER } from "@easyapply/shared";

export type FailureKind =
  | "session_required"
  | "session_changed"
  | "not_found"
  | "conflict"
  | "refused"
  | "api_unreachable"
  | "network"
  | "timeout"
  | "unexpected_response";

export interface ApiFailure {
  ok: false;
  kind: FailureKind;
  /** The HTTP status, when there was an answer. */
  status: number | null;
  /** The error body's `code`, when it had one. */
  code: string | null;
  /** The server's `detail`, or this extension's own sentence when there was none. */
  message: string;
}

export interface ApiSuccess<T> {
  ok: true;
  status: number;
  data: T;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export interface ApiRequest<T> {
  method: "GET" | "POST";
  /** Below /api/backend, e.g. `/jobs/12/requirements`. Built by ./calls.ts from checked ids. */
  path: string;
  /** JSON-serialisable; POST only. */
  body?: unknown;
  /** Whether the answer is the shape EasyApply sends for this request. */
  accept: (value: unknown) => value is T;
}

export interface TransportOptions {
  /** The web app's origin, e.g. http://localhost:3000. */
  webUrl: string;
  /** The account tag the request is for (./session.ts); null when the web app gave none. */
  sessionTag: string | null;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

export const DEFAULT_API_TIMEOUT_MS = 20_000;

export function backendUrl(webUrl: string, path: string): string {
  return new URL(`/api/backend${path}`, webUrl).href;
}

/**
 * THE way this extension sends a request to the web app's proxy: `init` as
 * given, plus `X-EasyApply-Session` when the account tag is known. Both
 * transports use it — `apiRequest` below and the save (./jobs.ts) — so no
 * backend request can leave without the tag of the account it is for. The
 * header is this function's alone: one in `init`, in any letter case, is
 * dropped rather than sent beside (or instead of) the real one.
 */
export function fetchBackend(
  fetchImpl: typeof fetch,
  url: string,
  init: RequestInit & { headers: Record<string, string> },
  sessionTag: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(init.headers)) {
    if (name.toLowerCase() !== SESSION_TAG_HEADER.toLowerCase()) headers[name] = value;
  }
  if (sessionTag !== null) headers[SESSION_TAG_HEADER] = sessionTag;
  return fetchImpl(url, { ...init, headers });
}

function parseBody(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function failure(kind: FailureKind, message: string, status: number | null = null, code: string | null = null): ApiFailure {
  return { ok: false, kind, status, code, message };
}

/** The error body's `detail` when it is a sentence; FastAPI's 422 list is summarised. */
function detailOf(body: Record<string, unknown>): string | null {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return "EasyApply refused the request as malformed.";
  return null;
}

export async function apiRequest<T>(
  request: ApiRequest<T>,
  { webUrl, sessionTag, fetch: fetchImpl = fetch, timeoutMs = DEFAULT_API_TIMEOUT_MS }: TransportOptions,
): Promise<ApiResult<T>> {
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
      backendUrl(webUrl, request.path),
      {
        method: request.method,
        headers:
          request.method === "POST"
            ? { "Content-Type": "application/json", Accept: "application/json" }
            : { Accept: "application/json" },
        body: request.method === "POST" ? JSON.stringify(request.body ?? {}) : undefined,
        // The browser's session for the web app, exactly as the web UI presents
        // it. The web app decides who that is; this extension never reads it.
        credentials: "include",
        cache: "no-store",
        // A redirect would be somebody else answering.
        redirect: "error",
        signal: controller.signal,
      },
      sessionTag,
    );
    text = await response.text();
  } catch {
    return timedOut
      ? failure("timeout", `EasyApply did not answer within ${Math.round(timeoutMs / 1000)} seconds.`)
      : failure("network", `Could not reach EasyApply at ${webUrl}.`);
  } finally {
    clearTimeout(timer);
  }

  const body = parseBody(text);

  if (response.ok) {
    return request.accept(body)
      ? { ok: true, status: response.status, data: body }
      : failure(
          "unexpected_response",
          `Something at ${webUrl} answered, but not the way EasyApply does.`,
          response.status,
        );
  }

  const error = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  const code = typeof error.code === "string" ? error.code : null;
  const detail = detailOf(error);

  if (response.status === 502 && code === "api_unreachable") {
    return failure("api_unreachable", detail ?? "The EasyApply API is unreachable.", 502, code);
  }
  if (response.status === 401 && code === SESSION_REQUIRED_CODE) {
    return failure("session_required", detail ?? "You are not signed in to EasyApply.", 401, code);
  }
  if (response.status === 409 && code === SESSION_CHANGED_CODE) {
    return failure(
      "session_changed",
      detail ?? "This browser is signed in to EasyApply as a different account now.",
      409,
      code,
    );
  }
  if (detail === null) {
    return failure("unexpected_response", `EasyApply answered ${response.status} without saying why.`, response.status, code);
  }
  if (response.status === 404) return failure("not_found", detail, 404, code);
  if (response.status === 409) return failure("conflict", detail, 409, code);
  return failure("refused", detail, response.status, code);
}
