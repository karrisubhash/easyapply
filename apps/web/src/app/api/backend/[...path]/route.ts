/**
 * Server-side proxy to the FastAPI service.
 *
 * The browser never calls the API directly. Two reasons, both structural:
 * the API needs no CORS configuration, and the identity credential is attached
 * here, on the server, where it is never handed to client JavaScript — the web
 * UI's or the Chrome extension's.
 *
 * Every request passes these steps, in this order:
 *
 *   1. Admission (src/server/request-guard.ts): a Host this server answers
 *      for, and — for anything that changes state — proof the request came
 *      from this origin, or is one of the EasyApply extension's three workflow
 *      writes (save a job, match it, start a tailored resume).
 *   2. Identity (src/server/auth/identity.ts): who the request is for, from
 *      this server's configuration or the browser's session cookie. The same
 *      for both clients. No session is a 401 `session_required`, answered
 *      here and never forwarded.
 *   3. Consistency: a request carrying X-EasyApply-Session (the account tag
 *      the client was rendered for) that is not the tag of the account the
 *      cookie now says is a 409 `session_changed`, never forwarded — a stale
 *      tab or an open extension popup must not act for whoever signed in since.
 *      After identity, so a signed-out browser reads as signed out.
 *   4. Operators: under real sign-in /internal/* is answered only for a
 *      holder of the operator app role; anyone else gets the 404 an unknown
 *      path gets, and the operator token is attached only after this check.
 *   5. Forwarding, with the credential identity produced. Nothing the client
 *      sent is forwarded as identity: the upstream headers are built fresh.
 */

import { SESSION_CHANGED_CODE, SESSION_REQUIRED_CODE, SESSION_TAG_HEADER } from "@easyapply/shared";
import { NextRequest, NextResponse } from "next/server";

import { resolveIdentity } from "@/server/auth/identity";
import { requestCookies } from "@/server/auth/next";
import {
  CROSS_SITE_REFUSAL,
  extensionIdOf,
  HOST_REFUSAL,
  isAllowedExtensionWrite,
  isAllowedHost,
  isSameOrigin,
  refuse,
} from "@/server/request-guard";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * The operator token, attached only to internal paths, and only for operators.
 *
 * Server-only — never NEXT_PUBLIC_ — so it is not in any bundle and browser
 * JavaScript cannot read it. In the development modes every identity counts as
 * an operator (as it always has: the web app was never the operator boundary
 * there). Under real sign-in only a holder of the operator app role does, and
 * the API checks that role again in the assertion
 * (services/api/app/api/operator_identity.py), so the token alone is not
 * enough even if something in front of the API forwarded it.
 *
 * Never on ordinary user traffic: an operator credential on every request
 * would buy nothing.
 */
/** Read per request, like every other setting this route consults. */
function operatorToken(): string | undefined {
  return process.env.OPERATOR_API_TOKEN || undefined;
}

/** Internal operations endpoints, which carry the operator token. */
function isInternal(path: string[]): boolean {
  return path[0] === "internal";
}

/**
 * Every segment an API path is made of: letters, digits and `-_.~`, and never
 * only dots. Next hands this route each segment already percent-DECODED, so
 * `jobs/..%2Finternal/x` arrives as ["jobs", "../internal", "x"] — joined and
 * resolved, that is /api/v1/internal/x, past the operator check above, and
 * `..%2F..%2F` reaches paths outside /api/v1 altogether. Such a path is not
 * one this proxy forwards, whatever it resolves to.
 */
const SEGMENT = /^[A-Za-z0-9._~-]+$/;

function isForwardablePath(path: string[]): boolean {
  return path.length > 0 && path.every((segment) => SEGMENT.test(segment) && !/^\.+$/.test(segment));
}

/**
 * Response headers worth carrying back from the API.
 *
 * An allow-list rather than a copy of everything: hop-by-hop headers and
 * anything about the upstream connection are this proxy's business, not the
 * browser's, and forwarding a `Set-Cookie` from a service that has no session
 * concept would be a way for one to appear later by accident.
 */
const PASS_THROUGH_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "cache-control",
] as const;

/**
 * Whether this response is a document rather than an API answer.
 *
 * Decided from the upstream Content-Type, not from the path, so the tailored
 * resume download and any format added beside it work without this file
 * learning either of their URLs.
 */
function isBinary(contentType: string | null): boolean {
  if (!contentType) return false;
  return !contentType.includes("json");
}

/**
 * One structured line per request from a Chrome extension — admitted or not.
 *
 * The extension is the one client whose requests arrive from outside this
 * origin, so it is the one worth a trail on the server: which route (ids
 * replaced), the method, the status, how long it took, and which extension
 * said it was asking. Never who the request was for, never a body: the
 * identity is an email address, and bodies carry job postings.
 */
function logExtensionRequest(request: NextRequest, path: string[], status: number, ms: number): void {
  const extension = extensionIdOf(request);
  if (!extension) return;
  const route = `/${path.map((segment) => (/^\d+$/.test(segment) ? ":id" : segment)).join("/")}`;
  console.info(
    `proxy.extension ${JSON.stringify({ extension, method: request.method, route, status, ms: Math.round(ms) })}`,
  );
}

async function forward(request: NextRequest, path: string[]): Promise<Response> {
  const started = performance.now();
  const response = await admitAndForward(request, path);
  logExtensionRequest(request, path, response.status, performance.now() - started);
  return response;
}

async function admitAndForward(request: NextRequest, path: string[]): Promise<Response> {
  // 1. Admission.
  const host = request.headers.get("host");
  if (!isAllowedHost(host)) return refuse(...HOST_REFUSAL);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  if (hasBody && !isSameOrigin(request, host) && !isAllowedExtensionWrite(request, path)) {
    return refuse(...CROSS_SITE_REFUSAL);
  }
  // A cross-site write is refused as one whatever its path; anything else
  // whose path decodes into more than plain segments is not forwarded.
  if (!isForwardablePath(path)) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  // 2. Identity.
  const identity = await resolveIdentity(requestCookies(request));
  if (identity.status === "misconfigured") {
    // The reason names configuration, so it goes to this server's log, not
    // to whoever made the request.
    console.error(`auth.misconfigured: ${identity.reason}`);
    return refuse("Sign-in is misconfigured on this server.", "auth_misconfigured", 500);
  }
  if (identity.status === "signed_out") {
    return refuse("You are not signed in to EasyApply.", SESSION_REQUIRED_CODE, 401);
  }

  // 3. Consistency.
  const presentedTag = request.headers.get(SESSION_TAG_HEADER);
  if (presentedTag !== null && presentedTag !== identity.sessionTag) {
    return refuse(
      "This browser is now signed in to EasyApply as a different account. Reload to continue as that account.",
      SESSION_CHANGED_CODE,
      409,
    );
  }

  // 4. Operators. The same answer as a path that does not exist, so the
  // operator surface is not advertised to people who cannot use it.
  const internal = isInternal(path);
  if (internal && !identity.operator) {
    console.warn(`proxy.operator_refused ${JSON.stringify({ mode: identity.mode })}`);
    return NextResponse.json({ detail: "Not Found" }, { status: 404, headers: { "Cache-Control": "no-store" } });
  }

  // 5. Forwarding.
  const target = new URL(`/api/v1/${path.join("/")}`, API_BASE_URL);
  target.search = request.nextUrl.search;

  const headers: Record<string, string> = {
    // Anything, because a route may answer with a document. The API still
    // answers JSON for everything except an artifact download.
    Accept: "*/*",
    // Who this is, as identity decided it. The only identity that goes
    // upstream: the incoming request's own headers are never copied.
    ...(await identity.upstreamHeaders()),
  };

  const token = operatorToken();
  if (token && internal) {
    headers["X-Operator-Token"] = token;
  }

  // Forward the body as bytes and pass the caller's own Content-Type through.
  // Reading it as text and declaring application/json corrupts a file upload:
  // multipart carries a boundary in the Content-Type, and rewriting the header
  // leaves the server unable to find any part at all.
  const body = hasBody ? await request.arrayBuffer() : undefined;
  const contentType = request.headers.get("content-type");
  if (hasBody && contentType) {
    headers["Content-Type"] = contentType;
  }

  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      cache: "no-store",
    });

    const upstreamType = response.headers.get("content-type");

    // A download. Read as bytes and keep the API's own headers: the filename
    // and the content type were decided server-side, from the artifact row, and
    // re-deriving either here would be a second opinion free to be wrong.
    // Text() would also corrupt the file outright — a DOCX is a zip.
    if (response.ok && isBinary(upstreamType)) {
      const forwarded = new Headers();
      for (const name of PASS_THROUGH_HEADERS) {
        const value = response.headers.get(name);
        if (value) forwarded.set(name, value);
      }
      return new NextResponse(await response.arrayBuffer(), {
        status: response.status,
        headers: forwarded,
      });
    }

    const text = await response.text();
    return new NextResponse(text || null, {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    // The API being down is not a 500 from this app's perspective; it is an
    // upstream failure, and saying so lets the UI show the right message.
    return NextResponse.json(
      { detail: "The EasyApply API is unreachable.", code: "api_unreachable" },
      { status: 502 },
    );
  }
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}

export async function PUT(request: NextRequest, context: Context) {
  return forward(request, (await context.params).path);
}
