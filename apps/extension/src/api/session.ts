/**
 * Who this browser is signed in to EasyApply as — asked before anything is
 * saved, so the review screen can say which account a job will land in.
 *
 * The extension does not hold that identity; it asks the web app, whose answer
 * comes from the same seam the proxy uses to attach the credential upstream
 * (apps/web/src/server/auth/identity.ts). The request carries the browser's
 * session cookie for the web app (`credentials: "include"`); the extension
 * never sees the cookie itself — it is HttpOnly, and this extension has no
 * `cookies` permission — only what the web app says about the session: its
 * mode, whether it is for someone, a label to show, and the account's tag.
 *
 * Signed in is `authenticated === true` and nothing else. Under real sign-in
 * (Entra) the provider may report no email, and perhaps no name either; that
 * account is still signed in, and is called by `sessionLabel`'s phrase.
 *
 * The tag (`sessionTag`) names the ACCOUNT, not the person's display name: two
 * accounts can share an email. It is sent back on every backend request
 * (./client.ts), so the web app's proxy refuses a request made for an account
 * this browser is no longer signed in to. A missing or malformed tag is an
 * unknown account, never a refusal: the popup then treats nothing it
 * remembers as this account's.
 */

import { sessionLabel, WEB_AUTH_MODES, type WebAuthMode } from "@easyapply/shared";

export type SessionState =
  | {
      kind: "signed_in";
      mode: WebAuthMode;
      /** What to call the account: its email, else its name, else "your EasyApply account". Display only. */
      label: string;
      /** The account's tag, or null when the web app gave none (unknown account). */
      sessionTag: string | null;
    }
  | { kind: "signed_out" }
  /** The web app could not be asked. Saving may still work; it will say if not. */
  | { kind: "unknown"; message: string };

export interface SessionOptions {
  webUrl: string;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

/** The shape of a tag this extension will carry: bounded, and nothing but URL-safe characters. */
export const SESSION_TAG_LIMIT = 128;
const SESSION_TAG_PATTERN = /^[A-Za-z0-9_-]+$/;

/** Whether a value is a session tag the extension may send back as a header. */
export function isSessionTag(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= SESSION_TAG_LIMIT &&
    SESSION_TAG_PATTERN.test(value)
  );
}

/** A tag, or null for anything that is not one. */
function tagOrNull(value: unknown): string | null {
  return isSessionTag(value) ? value : null;
}

export function sessionEndpoint(webUrl: string): string {
  return new URL("/api/auth/session", webUrl).href;
}

/** Where to send someone to sign in: the web app's home page offers sign-in while signed out. */
export function signInUrl(webUrl: string): string {
  return new URL("/", webUrl).href;
}

function isMode(value: unknown): value is WebAuthMode {
  return (WEB_AUTH_MODES as readonly unknown[]).includes(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

/**
 * The web app's answer, when it is one: `SessionState` for a well-formed
 * `WebSession`, null for anything else. `name` and `sessionTag` may be absent
 * (servers older than them); a tag that is not a tag is no tag.
 */
function sessionStateOf(value: unknown): SessionState | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const body = value as Record<string, unknown>;
  if (!isMode(body.mode) || typeof body.authenticated !== "boolean") return null;
  if (!isNullableString(body.email)) return null;
  const name = body.name === undefined ? null : body.name;
  if (!isNullableString(name)) return null;
  if (body.authenticated !== true) return { kind: "signed_out" };
  return {
    kind: "signed_in",
    mode: body.mode,
    label: sessionLabel({ email: body.email, name }),
    sessionTag: tagOrNull(body.sessionTag),
  };
}

export async function getSession({
  webUrl,
  fetch: fetchImpl = fetch,
  timeoutMs = 10_000,
}: SessionOptions): Promise<SessionState> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(sessionEndpoint(webUrl), {
      headers: { Accept: "application/json" },
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    const body: unknown = response.ok ? await response.json().catch(() => null) : null;
    const state = sessionStateOf(body);
    if (state === null) {
      return {
        kind: "unknown",
        message: response.ok
          ? `Something at ${webUrl} answered, but not the way EasyApply does.`
          : `EasyApply answered ${response.status} when asked who you are signed in as.`,
      };
    }
    return state;
  } catch {
    return { kind: "unknown", message: `Could not reach EasyApply at ${webUrl}.` };
  } finally {
    clearTimeout(timer);
  }
}

const SESSION_KINDS = new Set(["signed_in", "signed_out", "unknown"]);

/** Whether a message answer is a `SessionState` — for the popup, which receives it from the worker. */
export function isSessionState(value: unknown): value is SessionState {
  if (typeof value !== "object" || value === null) return false;
  const state = value as { kind?: unknown; mode?: unknown; label?: unknown; sessionTag?: unknown; message?: unknown };
  if (!SESSION_KINDS.has(state.kind as string)) return false;
  if (state.kind === "signed_in") {
    return (
      isMode(state.mode) &&
      typeof state.label === "string" &&
      (state.sessionTag === null || isSessionTag(state.sessionTag))
    );
  }
  if (state.kind === "unknown") return typeof state.message === "string";
  return true;
}
