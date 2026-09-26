/**
 * POST /api/auth/sign-in — start a session for this browser.
 *
 *   AUTH_MODE=entra        a same-origin FORM post (`return_to`, optional): the
 *                          answer is a 303 to Microsoft, carrying state, nonce
 *                          and a PKCE challenge; the browser comes back to
 *                          /api/auth/callback. See src/server/auth/sign-in-flow.ts.
 *   AUTH_MODE=dev-session  the DEVELOPMENT sign-in: `{"email": "..."}` in, a
 *                          signed session cookie out. Not authentication —
 *                          anyone who can reach this page can be anyone — it is
 *                          the test suites' way of being several people.
 *   AUTH_MODE=development  404: this server acts as its configured identity.
 *
 * Same-origin only. Signing in is not something the extension does (it uses
 * whatever session the browser has), and a cross-site page must not be able to
 * sign the browser into an account of its choosing — login CSRF, which would
 * make the person's next saves land in the attacker's account.
 */

import type { NextRequest } from "next/server";

import { readAuthConfig } from "@/server/auth/config";
import { isUsableEmail, normalizeEmail } from "@/server/auth/email";
import { isHttps, issueSession, resolveIdentity, sessionCookie } from "@/server/auth/identity";
import { misconfigured, sessionResponse } from "@/server/auth/responses";
import { beginSignIn } from "@/server/auth/sign-in-flow";
import { CROSS_SITE_REFUSAL, HOST_REFUSAL, isAllowedHost, isSameOrigin, refuse } from "@/server/request-guard";

/** A sign-in body is one short field; anything larger is not one. */
const MAX_BODY_BYTES = 1024;

async function readEmail(request: NextRequest): Promise<string | null> {
  // Refused on the declared length before anything is read.
  if (Number(request.headers.get("content-length") ?? 0) > MAX_BODY_BYTES) return null;
  const text = await request.text();
  if (text.length > MAX_BODY_BYTES) return null;
  try {
    const body: unknown = JSON.parse(text);
    if (typeof body !== "object" || body === null) return null;
    const email = (body as { email?: unknown }).email;
    return typeof email === "string" ? normalizeEmail(email) : null;
  } catch {
    return null;
  }
}

export async function POST(request: NextRequest) {
  const host = request.headers.get("host");
  if (!isAllowedHost(host)) return refuse(...HOST_REFUSAL);
  if (!isSameOrigin(request, host)) return refuse(...CROSS_SITE_REFUSAL);

  const config = readAuthConfig();
  if (config.mode === "misconfigured") return misconfigured(config.reason);
  if (config.mode === "entra") return beginSignIn(request, config);
  if (config.mode !== "dev-session") {
    return refuse(
      "Signing in is not used here: this server acts as its configured identity (AUTH_MODE=development).",
      "sign_in_not_available",
      404,
    );
  }

  const email = await readEmail(request);
  if (!email || !isUsableEmail(email)) {
    return refuse("Enter an email address.", "invalid_email", 422);
  }

  const token = await issueSession(email, config);
  const identity = await resolveIdentity({ get: () => token }, config);
  if (identity.status === "misconfigured") return misconfigured(identity.reason);
  return sessionResponse(identity, { setCookie: sessionCookie(token, config.maxAgeSeconds, isHttps(request)) });
}
