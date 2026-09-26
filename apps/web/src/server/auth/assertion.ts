/**
 * How the API is told who a request is for under real sign-in: a short signed
 * assertion, minted here per proxied request.
 *
 *   Authorization: Bearer <HS256 JWT>
 *     iss "easyapply-web"   aud "easyapply-api"
 *     sub  the Entra `oid`  idp the Entra issuer the ID token was validated against
 *     tid  the tenant       sid the EasyApply session it was minted from
 *     email, name           display metadata only (absent when unknown)
 *     roles                 ["<operator role>"] for an operator, else absent
 *     iat, nbf, exp = iat + 60, jti (unique; the API accepts each once)
 *
 * Why not forward a Microsoft access token: the API would need one per
 * request, so the web server would have to keep refresh tokens server-side and
 * refresh them under concurrency — a token store and its races, for a web
 * server that already validated the sign-in once. Instead the web server, which
 * is the only thing holding the session, vouches for it with a key only it and
 * the API share (IDENTITY_ASSERTION_SECRET — never the session secret). The API
 * validates every claim (services/api/app/api/assertion_identity.py).
 *
 * Never sent to a browser, never logged.
 */

import { SignJWT } from "jose";

import type { EntraConfig } from "./config";
import type { EntraSessionClaims } from "./entra-session";
import { nowSeconds, toBase64Url } from "./session-token";

export const ASSERTION_ISSUER = "easyapply-web";
export const ASSERTION_AUDIENCE = "easyapply-api";
export const ASSERTION_LIFETIME_SECONDS = 60;

const encoder = new TextEncoder();

export async function mintAssertion(
  session: EntraSessionClaims,
  entra: Pick<EntraConfig, "operatorRole">,
  secret: string,
  now: number = nowSeconds(),
): Promise<string> {
  const claims: Record<string, unknown> = { idp: session.iss, tid: session.tid, sid: session.sid };
  if (session.email) claims.email = session.email;
  if (session.name) claims.name = session.name;
  if (session.op) claims.roles = [entra.operatorRole];

  return new SignJWT(claims)
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer(ASSERTION_ISSUER)
    .setAudience(ASSERTION_AUDIENCE)
    .setSubject(session.oid)
    .setIssuedAt(now)
    .setNotBefore(now)
    .setExpirationTime(now + ASSERTION_LIFETIME_SECONDS)
    .setJti(toBase64Url(crypto.getRandomValues(new Uint8Array(16))))
    .sign(encoder.encode(secret));
}
