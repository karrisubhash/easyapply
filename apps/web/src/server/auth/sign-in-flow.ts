/**
 * Real sign-in, end to end: the two halves of the authorization code flow.
 *
 *   beginSignIn     POST /api/auth/sign-in (same-origin form). Seals a
 *                   transaction into its own cookie and sends the browser to
 *                   Microsoft with state, nonce and a PKCE challenge.
 *   completeSignIn  GET /api/auth/callback (Microsoft sends the browser back).
 *                   Opens the transaction `state` names, redeems the code,
 *                   validates the ID token, issues the EasyApply session and
 *                   discards every Microsoft token.
 *
 * Every failure lands on `${APP_ORIGIN}/?auth_error=<code>`, one of
 *   cancelled    the person backed out at Microsoft
 *   expired      the sign-in took over ten minutes, or its code was already used
 *   unavailable  Microsoft could not be reached (discovery, keys, token endpoint)
 *   failed       anything else — including every validation failure
 * and writes one `auth.sign_in.failure` line with a reason category (and the
 * provider's own error code and AADSTS number when it gave them). Never a
 * token, a code, a claim or an email. A failed sign-in does not touch a session
 * the browser already holds.
 */

import type { NextRequest } from "next/server";

import type { AuthConfig } from "./config";
import { issueEntraSession, sessionCookie, sessionCookieName } from "./identity";
import { MAX_ENTRA_TOKEN_LENGTH } from "./entra-session";
import { authorizationUrl, discover, exchangeCode, SignInError, validateIdToken } from "./oidc";
import { nowSeconds } from "./session-token";
import {
  openTransaction,
  pkceChallenge,
  randomToken,
  safeReturnTo,
  sealTransaction,
  TRANSACTION_MAX_AGE_SECONDS,
  transactionCookieName,
} from "./transaction";

type EntraAuth = Extract<AuthConfig, { mode: "entra" }>;

export type SignInFailure = "cancelled" | "expired" | "unavailable" | "failed";

const CALLBACK_PATH = "/api/auth/callback";
const MAX_FORM_BYTES = 2048;
const STATE_SHAPE = /^[A-Za-z0-9_-]{16,128}$/;
const UNAVAILABLE = new Set([
  "discovery_unreachable",
  "discovery_invalid",
  "discovery_issuer_mismatch",
  "token_endpoint_unreachable",
  "jwks_unreachable",
]);

/**
 * Over https the `__Host-` prefix: browsers accept it only with Secure,
 * Path=/ and no Domain, so neither plain http nor a sibling subdomain can
 * plant a transaction of its own for the callback to accept. (`__Secure-`
 * would still allow Domain=, which is the sibling-subdomain case.) The price is
 * Path=/ — the sealed ~300 bytes ride on every request for up to ten minutes.
 */
async function txCookieName(state: string, config: EntraAuth): Promise<string> {
  return `${config.secureCookies ? "__Host-" : ""}${await transactionCookieName(state)}`;
}

function txPath(secure: boolean): string {
  return secure ? "/" : CALLBACK_PATH;
}

function redirect(location: string): Response {
  // 303: the browser follows with a GET whatever the method was.
  return new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "no-store" } });
}

export function transactionCookie(name: string, value: string, secure: boolean): string {
  return [
    `${name}=${value}`,
    `Path=${txPath(secure)}`,
    `Max-Age=${TRANSACTION_MAX_AGE_SECONDS}`,
    "HttpOnly",
    "SameSite=Lax",
    ...(secure ? ["Secure"] : []),
  ].join("; ");
}

function clearedTransactionCookie(name: string, secure: boolean): string {
  return [`${name}=`, `Path=${txPath(secure)}`, "Max-Age=0", "HttpOnly", "SameSite=Lax", ...(secure ? ["Secure"] : [])].join("; ");
}

function logFailure(reason: string, provider: { error?: string; code?: number } = {}): void {
  const entry: Record<string, unknown> = { reason };
  if (provider.error) entry.error = /^[a-z_]{1,64}$/.test(provider.error) ? provider.error : "other";
  if (provider.code !== undefined) entry.aadsts = provider.code;
  console.warn(`auth.sign_in.failure ${JSON.stringify(entry)}`);
}

function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return difference === 0;
}

/** The request's host is the one Microsoft will send the browser back to. */
function isAppHost(request: Request, appOrigin: string): boolean {
  const host = request.headers.get("host");
  if (!host) return false;
  try {
    const origin = new URL(appOrigin);
    return new URL(`${origin.protocol}//${host}`).host === origin.host;
  } catch {
    return false;
  }
}

async function readReturnTo(request: Request): Promise<string | null> {
  if (Number(request.headers.get("content-length") ?? 0) > MAX_FORM_BYTES) return null;
  const type = request.headers.get("content-type") ?? "";
  if (!type.toLowerCase().startsWith("application/x-www-form-urlencoded")) return null;
  const text = await request.text();
  if (text.length > MAX_FORM_BYTES) return null;
  return new URLSearchParams(text).get("return_to");
}

export async function beginSignIn(request: NextRequest, config: EntraAuth): Promise<Response> {
  // Sign in where Microsoft will return: a transaction cookie set on another
  // host name for this server (localhost vs 127.0.0.1) would never reach the
  // callback, and the sign-in would fail as "expired".
  if (!isAppHost(request, config.appOrigin)) return redirect(`${config.appOrigin}/`);

  const returnTo = safeReturnTo(await readReturnTo(request), config.appOrigin);

  let discovery;
  try {
    discovery = await discover(config.entra);
  } catch (error) {
    logFailure(error instanceof SignInError ? error.reason : "unexpected");
    return redirect(`${config.appOrigin}/?auth_error=unavailable`);
  }

  const state = randomToken();
  const nonce = randomToken();
  const verifier = randomToken();
  const sealed = await sealTransaction({ state, nonce, verifier, returnTo, iat: nowSeconds() }, config.secret);
  const response = redirect(authorizationUrl(discovery, config.entra, { state, nonce, challenge: await pkceChallenge(verifier) }));
  response.headers.append("Set-Cookie", transactionCookie(await txCookieName(state, config), sealed, config.secureCookies));
  return response;
}

export async function completeSignIn(request: NextRequest, config: EntraAuth): Promise<Response> {
  const params = request.nextUrl.searchParams;
  const secure = config.secureCookies;
  let cookieName: string | null = null;

  const fail = (code: SignInFailure, reason: string, provider?: { error?: string; code?: number }): Response => {
    logFailure(reason, provider);
    const response = redirect(`${config.appOrigin}/?auth_error=${code}`);
    // Only THIS transaction's cookie: another tab's sign-in may still be under way.
    if (cookieName) response.headers.append("Set-Cookie", clearedTransactionCookie(cookieName, secure));
    return response;
  };

  // The state first, before anything the provider says is believed: an
  // answer that does not belong to a sign-in this browser started is not
  // an answer at all (login CSRF).
  const state = params.get("state");
  if (!state || !STATE_SHAPE.test(state)) return fail("failed", "state_missing");
  cookieName = await txCookieName(state, config);
  const secrets = config.previousSecret ? [config.secret, config.previousSecret] : [config.secret];
  const transaction = await openTransaction(request.cookies.get(cookieName)?.value, secrets);
  if (!transaction || !sameText(transaction.state, state)) return fail("expired", "transaction_missing");

  const providerError = params.get("error");
  if (providerError !== null) {
    const aadsts = /AADSTS(\d{4,6})/.exec(params.get("error_description") ?? "")?.[1];
    return fail(providerError === "access_denied" ? "cancelled" : "failed", "provider_error", {
      error: providerError.slice(0, 64),
      code: aadsts ? Number(aadsts) : undefined,
    });
  }

  const code = params.get("code");
  if (!code || code.length > 4096) return fail("failed", "code_missing");

  try {
    const discovery = await discover(config.entra);
    const idToken = await exchangeCode(discovery, config.entra, { code, verifier: transaction.verifier });
    let signIn = await validateIdToken(idToken, discovery, config.entra, transaction.nonce);

    let token = await issueEntraSession(signIn, config);
    if (token.length > MAX_ENTRA_TOKEN_LENGTH) {
      // Display metadata so large the cookie would be refused: drop it rather
      // than sign someone in to a session that immediately reads as signed out.
      signIn = { ...signIn, email: null, name: null };
      token = await issueEntraSession(signIn, config);
    }

    const response = redirect(`${config.appOrigin}${transaction.returnTo}`);
    response.headers.append("Set-Cookie", sessionCookie(token, config.maxAgeSeconds, secure, sessionCookieName(config)));
    response.headers.append("Set-Cookie", clearedTransactionCookie(cookieName, secure));
    console.info(
      `auth.sign_in.success ${JSON.stringify({ operator: signIn.roles.includes(config.entra.operatorRole) })}`,
    );
    return response;
  } catch (error) {
    if (error instanceof SignInError) {
      const category: SignInFailure = UNAVAILABLE.has(error.reason)
        ? "unavailable"
        : error.reason === "token_endpoint_refused" && error.providerError.error === "invalid_grant"
          ? "expired"
          : "failed";
      return fail(category, error.reason, error.providerError);
    }
    return fail("failed", "unexpected");
  }
}
