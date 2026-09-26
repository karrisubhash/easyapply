/**
 * The development session: a signed, expiring claim of who this browser is.
 *
 *   v1.<base64url(JSON {sub, iat, exp})>.<base64url(HMAC-SHA256(secret, "v1." + payload))>
 *
 * Signed, not encrypted: it carries an email the person typed themselves and
 * nothing else. Signing is what matters — a cookie a browser can edit must not
 * be able to name somebody else. Verified with `crypto.subtle.verify`, which
 * compares in constant time. Web Crypto rather than node:crypto, so the same
 * code runs in any Next runtime.
 *
 * This is the development issuer. When Entra External ID replaces it, the
 * session holds (a reference to) the tokens an OIDC code flow returned; the
 * cookie's attributes and the routes around it stay as they are.
 */

import { isUsableEmail } from "./email";

const VERSION = "v1";
/** A tolerance for clocks, not for replay: a token from the future is refused past this. */
const CLOCK_SKEW_SECONDS = 60;
/** No legitimate token is anywhere near this; a longer cookie is not parsed at all. */
const MAX_TOKEN_LENGTH = 2048;

export interface SessionClaims {
  /** The email the session is for. */
  sub: string;
  /** Issued at, seconds since the epoch. */
  iat: number;
  /** Expires at, seconds since the epoch. */
  exp: number;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

export function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromBase64Url(text: string): Uint8Array<ArrayBuffer> | null {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) return null;
  try {
    const binary = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  } catch {
    return null;
  }
}

export function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

export async function signSession(claims: SessionClaims, secret: string): Promise<string> {
  const payload = toBase64Url(encoder.encode(JSON.stringify(claims)));
  const signed = `${VERSION}.${payload}`;
  const signature = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(signed));
  return `${signed}.${toBase64Url(new Uint8Array(signature))}`;
}

function isClaims(value: unknown): value is SessionClaims {
  if (typeof value !== "object" || value === null) return false;
  const claims = value as Record<string, unknown>;
  return (
    typeof claims.sub === "string" &&
    Number.isInteger(claims.iat) &&
    Number.isInteger(claims.exp) &&
    Object.keys(claims).length === 3
  );
}

/**
 * The claims, if this token was signed with this secret and is current; else
 * null. Every way of being wrong is the same null — the caller has nothing to
 * gain from knowing which, and neither does whoever made the token.
 */
export async function verifySession(
  token: string | undefined,
  secret: string,
  now: number = nowSeconds(),
): Promise<SessionClaims | null> {
  if (!token || token.length > MAX_TOKEN_LENGTH) return null;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== VERSION) return null;
  const [version, payload, signature] = parts as [string, string, string];

  const signatureBytes = fromBase64Url(signature);
  if (!signatureBytes) return null;
  const valid = await crypto.subtle.verify(
    "HMAC",
    await hmacKey(secret),
    signatureBytes,
    encoder.encode(`${version}.${payload}`),
  );
  if (!valid) return null;

  const payloadBytes = fromBase64Url(payload);
  if (!payloadBytes) return null;
  let claims: unknown;
  try {
    claims = JSON.parse(decoder.decode(payloadBytes));
  } catch {
    return null;
  }

  if (!isClaims(claims) || !isUsableEmail(claims.sub)) return null;
  if (claims.exp <= now || claims.iat > now + CLOCK_SKEW_SECONDS || claims.iat >= claims.exp) return null;
  return claims;
}
