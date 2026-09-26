/**
 * Admission: who may reach this server's routes at all, and for what.
 *
 * Shared by every route that answers a browser on the user's behalf — the API
 * proxy (`/api/backend/*`) and the session routes (`/api/auth/*`) — so that a
 * new route cannot quietly answer on weaker terms than the proxy does.
 *
 * Admission is deliberately a different question from identity
 * (src/server/auth/identity.ts). Admission is about WHERE a request came from
 * and differs by client: the web UI is same-origin, the Chrome extension is its
 * own origin and may make exactly one write. Identity is about WHO the request
 * is for, and is decided the same way for both. A request must pass admission
 * before identity is even looked at, so a cross-site page cannot learn whether
 * a session exists.
 *
 * The rules were written for the hardest case: in `AUTH_MODE=development` the
 * configured identity is attached to EVERY admitted request, with no session
 * at all, so admission is the only thing standing between a hostile page and
 * that identity. A session (`dev-session`, and a real sign-in later) only
 * narrows who is attached; it never replaces these checks.
 *
 *   - Host. Only loopback names (plus an explicit PROXY_ALLOWED_HOSTS list) are
 *     answered, on every method. Without it, DNS rebinding — a hostile domain
 *     re-pointed at 127.0.0.1 — makes a hostile page *same-origin* with this
 *     server, and it could read anything the identity can.
 *   - Origin, for anything that is not GET or HEAD. A page on any other site
 *     can make the browser POST here (a multipart form needs no preflight),
 *     carrying the session cookie: cross-site request forgery. A state-changing
 *     request must prove it came from this origin — with one exception, opt-in
 *     and three routes wide: the EasyApply Chrome extension's own workflow
 *     (save a job, match it, start a tailored resume for it).
 */

import { NextResponse } from "next/server";

/** Hostnames this server answers for whatever the configuration says. */
const LOOPBACK_HOSTNAMES: ReadonlySet<string> = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * A Host header is `hostname[:port]` and nothing more.
 *
 * Checked by shape BEFORE parsing, so that nothing with userinfo or a path in
 * it (`localhost@evil.example`, `evil.example/localhost`) reaches the URL
 * parser, which would happily read a different hostname out of it.
 */
const HOST_SHAPE = /^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$/;

/** The hostname a Host header names, lower-cased, or null if it is not one. */
function hostnameOf(host: string): string | null {
  if (!HOST_SHAPE.test(host)) return null;
  try {
    return new URL(`http://${host}`).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * Loopback, plus PROXY_ALLOWED_HOSTS: comma-separated hostnames, any port.
 *
 * Read per request rather than at import, so the list is exactly what the
 * process environment says now. Empty by default — a development server is
 * reached by loopback and needs nothing else.
 */
export function isAllowedHost(host: string | null): host is string {
  if (!host) return false;
  const hostname = hostnameOf(host);
  if (!hostname) return false;
  return isAllowedHostname(hostname, process.env.PROXY_ALLOWED_HOSTS);
}

/** The same rule for a bare hostname against a given list (the auth configuration checks APP_ORIGIN with it). */
export function isAllowedHostname(hostname: string, allowed: string | undefined): boolean {
  const name = hostname.toLowerCase();
  if (LOOPBACK_HOSTNAMES.has(name)) return true;
  return (allowed ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .some((entry) => hostnameOf(entry) === name);
}

/**
 * Whether a state-changing request provably came from this origin.
 *
 * Browsers attach Sec-Fetch-Site and Origin themselves and page script cannot
 * forge either, so between them they say where a request came from:
 *
 *   - Sec-Fetch-Site, when present, must be `same-origin`. `same-site` is
 *     refused too: another port on localhost is another application.
 *   - Origin, when present, must name the host this request was sent to.
 *     `null` (a sandboxed frame, a file: page) names nothing and is refused.
 *   - At least one must be present. Every current browser sends Origin on a
 *     POST, so a request with neither did not come from a page — and refusing
 *     it costs only a hand-written curl, which can send an Origin.
 *
 * GET and HEAD are not checked: they change nothing, and a cross-site page
 * cannot read their responses (the Host check stops the rebinding attack that
 * could).
 */
export function isSameOrigin(request: Request, host: string): boolean {
  const site = request.headers.get("sec-fetch-site");
  const origin = request.headers.get("origin");

  if (site === null && origin === null) return false;
  if (site !== null && site !== "same-origin") return false;
  if (origin === null) return true;

  try {
    const from = new URL(origin);
    // Normalised through the same parser, so a default port on either side
    // (`:80`, or its absence) cannot make equal hosts differ.
    return from.host === new URL(`${from.protocol}//${host}`).host;
  } catch {
    return false;
  }
}

/** A Chrome extension's origin: `chrome-extension://` and a 32-letter id, a–p. */
const EXTENSION_ORIGIN = /^chrome-extension:\/\/([a-p]{32})$/;

/**
 * Extension ids allowed to save a job: PROXY_ALLOWED_EXTENSION_IDS,
 * comma-separated. Empty by default, so no extension can write through this
 * proxy until a developer names one. Read per request, like PROXY_ALLOWED_HOSTS.
 */
function allowedExtensionIds(): Set<string> {
  return new Set(
    (process.env.PROXY_ALLOWED_EXTENSION_IDS ?? "")
      .split(",")
      .map((entry) => entry.trim().toLowerCase())
      .filter((entry) => /^[a-p]{32}$/.test(entry)),
  );
}

/**
 * The writes the extension's workflow consists of, and nothing else:
 *
 *   POST /jobs                          save the posting on screen (idempotent:
 *                                       the same posting answers 200, unchanged)
 *   POST /jobs/<id>/matches             match the current confirmed profile
 *                                       version (create-or-return: the same
 *                                       inputs answer with the stored match)
 *   POST /jobs/<id>/tailored-resumes    start a tailored resume (a second start
 *                                       joins the run already in flight)
 *
 * Each creates a record in the person's own account from things already in it;
 * none edits, confirms or deletes anything, none touches the profile, a resume
 * upload, reprocessing, /internal or sign-in. Those stay same-origin only. The
 * id must be a plain positive integer — no sign, no leading zero, no encoded
 * slash — and the API still resolves it inside the caller's own account, so a
 * foreign id is a 404 there.
 */
const ID_SEGMENT = /^[1-9]\d{0,17}$/;

function isExtensionWritePath(path: string[]): boolean {
  if (path.length === 1) return path[0] === "jobs";
  return (
    path.length === 3 &&
    path[0] === "jobs" &&
    ID_SEGMENT.test(path[1] ?? "") &&
    (path[2] === "matches" || path[2] === "tailored-resumes")
  );
}

/** The extension id a request's Origin names, if it is an extension's origin at all. */
export function extensionIdOf(request: Request): string | null {
  return EXTENSION_ORIGIN.exec(request.headers.get("origin") ?? "")?.[1] ?? null;
}

/**
 * Whether this is the EasyApply extension making one of its workflow writes.
 *
 * The extension (apps/extension) has no page on this origin to be same-origin
 * with, so without this it could not save anything — and it must come through
 * here rather than go to the API, because this is where identity is attached.
 * It sends no identity of its own; the browser attaches this server's session
 * cookie to its request exactly as it does to the web UI's (measured: Chrome
 * sends HttpOnly cookies, SameSite Lax and Strict alike, on a service-worker
 * fetch from an extension holding host permission for this server), and the
 * job is saved for whoever that session says — see docs/authentication.md.
 *
 * What makes it safe to let through is what a hostile page cannot produce:
 *
 *   - Origin `chrome-extension://<id>`. Browsers set Origin and page script
 *     cannot forge it; the only context that sends this one is that
 *     extension's own service worker or pages. (Its content scripts do not:
 *     their requests carry the page's origin.)
 *   - Sec-Fetch-Site `none`, or absent. Chrome sends `none` for a request from
 *     an extension that holds host permission for this server; a request a web
 *     page causes is `same-origin`, `same-site` or `cross-site`, never `none`.
 *   - The id is one PROXY_ALLOWED_EXTENSION_IDS names.
 *   - It is one of the three writes above and nothing else. The extension has
 *     no business confirming a profile, uploading a resume, reprocessing,
 *     signing anyone in or out, or reaching /internal — all of which stay
 *     same-origin only. (Widened from POST /jobs alone on 2026-09-26, when the
 *     extension gained matching and tailoring: docs/chrome-extension.md §9.)
 *
 * Something that is not a browser can send any headers it likes, including
 * these; it could equally send a same-origin Origin. Admission is a defence
 * against what a browser can be made to do, not an authorization boundary.
 */
export function isAllowedExtensionWrite(request: Request, path: string[]): boolean {
  if (request.method !== "POST" || !isExtensionWritePath(path)) return false;

  const site = request.headers.get("sec-fetch-site");
  if (site !== null && site !== "none") return false;

  const match = EXTENSION_ORIGIN.exec(request.headers.get("origin") ?? "");
  return match !== null && allowedExtensionIds().has(match[1]);
}

/** A refusal, in the shape every other error from these routes already has. */
export function refuse(detail: string, code: string, status = 403): Response {
  return NextResponse.json({ detail, code }, { status, headers: { "Cache-Control": "no-store" } });
}

export const HOST_REFUSAL = ["This server does not answer for that host.", "host_not_allowed"] as const;
export const CROSS_SITE_REFUSAL = ["Cross-site requests are not accepted.", "cross_site_request"] as const;
