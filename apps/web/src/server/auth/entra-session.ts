/**
 * The EasyApply session after a real sign-in (AUTH_MODE=entra).
 *
 *   v2.<base64url(JSON claims)>.<base64url(HMAC-SHA256(secret, "v2." + payload))>
 *
 *   claims  typ   "entra" — so a token of another kind can never pass as one
 *           iss   the Entra issuer the ID token was validated against
 *           aud   this application's client id — so a session issued by another
 *                 deployment (even one sharing the secret) is not one here
 *           tid   the tenant GUID;  oid  the person's immutable object id
 *           email, name   display only (either may be null)
 *           op    whether the ID token carried the operator app role
 *           sid   16 random bytes minted HERE, per EasyApply session — never
 *                 an Entra claim (not always present, and not ours)
 *           iat, exp
 *
 * Issued once, at the callback, after the web server validated the ID token.
 * No Microsoft token is in it or kept anywhere. Signed, not encrypted: it holds
 * nothing the person does not already know about themselves, and signing is
 * what stops a browser from editing it into somebody else.
 *
 * A dev-session (v1) token is NOT an entra session and vice versa — the version
 * prefix is signed and `typ` is checked — so reusing a secret across modes
 * cannot turn a development sign-in into a real one. Verification accepts the
 * current secret and, during a rotation, the previous one; signing uses only
 * the current one.
 */

import { fromBase64Url, hmacKey, nowSeconds, toBase64Url } from "./session-token";

const VERSION = "v2";
const CLOCK_SKEW_SECONDS = 60;
/** A cookie must stay under 4 KB with its name and attributes. */
export const MAX_ENTRA_TOKEN_LENGTH = 3072;
const EMAIL_MAX = 320;
const NAME_MAX = 255;

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SID = /^[A-Za-z0-9_-]{16,64}$/;

export interface EntraSessionClaims {
  typ: "entra";
  iss: string;
  aud: string;
  tid: string;
  oid: string;
  email: string | null;
  name: string | null;
  op: boolean;
  sid: string;
  iat: number;
  exp: number;
}

const KEYS = ["typ", "iss", "aud", "tid", "oid", "email", "name", "op", "sid", "iat", "exp"];

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

export async function signEntraSession(claims: EntraSessionClaims, secret: string): Promise<string> {
  const payload = toBase64Url(encoder.encode(JSON.stringify(claims)));
  const signed = `${VERSION}.${payload}`;
  const signature = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(signed));
  return `${signed}.${toBase64Url(new Uint8Array(signature))}`;
}

function nullableText(value: unknown, limit: number): boolean {
  return value === null || (typeof value === "string" && value.length > 0 && Array.from(value).length <= limit);
}

function isClaims(value: unknown): value is EntraSessionClaims {
  if (typeof value !== "object" || value === null) return false;
  const claims = value as Record<string, unknown>;
  const keys = Object.keys(claims);
  return (
    keys.length === KEYS.length &&
    KEYS.every((key) => keys.includes(key)) &&
    claims.typ === "entra" &&
    typeof claims.iss === "string" &&
    claims.iss.length > 0 &&
    claims.iss.length <= 512 &&
    typeof claims.aud === "string" &&
    GUID.test(claims.aud) &&
    typeof claims.tid === "string" &&
    GUID.test(claims.tid) &&
    typeof claims.oid === "string" &&
    GUID.test(claims.oid) &&
    nullableText(claims.email, EMAIL_MAX) &&
    nullableText(claims.name, NAME_MAX) &&
    typeof claims.op === "boolean" &&
    typeof claims.sid === "string" &&
    SID.test(claims.sid) &&
    Number.isInteger(claims.iat) &&
    Number.isInteger(claims.exp)
  );
}

/**
 * The claims, if this token was signed with one of these secrets, is an entra
 * session, and is current; else null — every way of being wrong alike.
 */
export async function verifyEntraSession(
  token: string | undefined,
  secrets: readonly string[],
  now: number = nowSeconds(),
): Promise<EntraSessionClaims | null> {
  if (!token || token.length > MAX_ENTRA_TOKEN_LENGTH) return null;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== VERSION) return null;
  const [version, payload, signature] = parts as [string, string, string];
  const signatureBytes = fromBase64Url(signature);
  if (!signatureBytes) return null;

  let valid = false;
  for (const secret of secrets) {
    if (!secret) continue;
    if (await crypto.subtle.verify("HMAC", await hmacKey(secret), signatureBytes, encoder.encode(`${version}.${payload}`))) {
      valid = true;
      break;
    }
  }
  if (!valid) return null;

  const payloadBytes = fromBase64Url(payload);
  if (!payloadBytes) return null;
  let claims: unknown;
  try {
    claims = JSON.parse(decoder.decode(payloadBytes));
  } catch {
    return null;
  }
  if (!isClaims(claims)) return null;
  if (claims.exp <= now || claims.iat > now + CLOCK_SKEW_SECONDS || claims.iat >= claims.exp) return null;
  return claims;
}

/** 16 random bytes, base64url: the per-session id (`sid`). */
export function newSessionId(): string {
  return toBase64Url(crypto.getRandomValues(new Uint8Array(16)));
}
