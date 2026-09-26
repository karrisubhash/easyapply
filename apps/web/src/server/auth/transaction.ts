/**
 * The sign-in transaction: what must survive the round trip to Microsoft.
 *
 * `state` (binds the callback to THIS browser's sign-in — login CSRF), `nonce`
 * (binds the ID token to it — token replay), the PKCE `verifier` (binds the
 * code to it — code interception) and where to return afterwards.
 *
 * Kept in the browser, sealed: AES-256-GCM under a key derived from
 * SESSION_SECRET with HKDF for this purpose alone, so the cookie can be neither
 * read nor edited, and a session key never doubles as an encryption key. One
 * cookie PER TRANSACTION, named after its state, so two tabs signing in at once
 * do not overwrite each other; the callback opens the one its `state` names and
 * clears it, whatever the outcome. Ten minutes, then it is refused.
 *
 *   easyapply_oidc_<16 chars of base64url(SHA-256(state))>
 *   HttpOnly; SameSite=Lax (the callback is a top-level GET from Microsoft,
 *   which Lax cookies ride on); Path=/api/auth/callback; Max-Age=600;
 *   Secure when the app is served over https.
 */

import { fromBase64Url, nowSeconds, toBase64Url } from "./session-token";

export const TRANSACTION_COOKIE_PREFIX = "easyapply_oidc_";
export const TRANSACTION_MAX_AGE_SECONDS = 600;
const CLOCK_SKEW_SECONDS = 60;

export interface SignInTransaction {
  state: string;
  nonce: string;
  verifier: string;
  returnTo: string;
  iat: number;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

async function sealingKey(secret: string): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", encoder.encode(secret), "HKDF", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: encoder.encode("easyapply"), info: encoder.encode("oidc-transaction-v1") },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

/** 32 random bytes, base64url. */
export function randomToken(): string {
  return toBase64Url(crypto.getRandomValues(new Uint8Array(32)));
}

/** base64url(SHA-256(verifier)) — the PKCE S256 challenge. */
export async function pkceChallenge(verifier: string): Promise<string> {
  return toBase64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(verifier))));
}

/** The cookie name for a transaction, derived from its state. */
export async function transactionCookieName(state: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(state)));
  return `${TRANSACTION_COOKIE_PREFIX}${toBase64Url(digest).slice(0, 16)}`;
}

export async function sealTransaction(transaction: SignInTransaction, secret: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode("easyapply-oidc") },
    await sealingKey(secret),
    encoder.encode(JSON.stringify(transaction)),
  );
  const bytes = new Uint8Array(iv.length + sealed.byteLength);
  bytes.set(iv, 0);
  bytes.set(new Uint8Array(sealed), iv.length);
  return toBase64Url(bytes);
}

function isTransaction(value: unknown): value is SignInTransaction {
  if (typeof value !== "object" || value === null) return false;
  const tx = value as Record<string, unknown>;
  return (
    Object.keys(tx).length === 5 &&
    typeof tx.state === "string" &&
    typeof tx.nonce === "string" &&
    typeof tx.verifier === "string" &&
    typeof tx.returnTo === "string" &&
    Number.isInteger(tx.iat)
  );
}

/** The transaction, if sealed by one of these secrets and not yet ten minutes old; else null. */
export async function openTransaction(
  value: string | undefined,
  secrets: readonly string[],
  now: number = nowSeconds(),
): Promise<SignInTransaction | null> {
  if (!value || value.length > 2048) return null;
  const bytes = fromBase64Url(value);
  if (!bytes || bytes.length < 13) return null;
  for (const secret of secrets) {
    if (!secret) continue;
    try {
      const plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: bytes.slice(0, 12), additionalData: encoder.encode("easyapply-oidc") },
        await sealingKey(secret),
        bytes.slice(12),
      );
      const parsed: unknown = JSON.parse(decoder.decode(new Uint8Array(plain)));
      if (!isTransaction(parsed)) return null;
      if (parsed.iat > now + CLOCK_SKEW_SECONDS || now - parsed.iat > TRANSACTION_MAX_AGE_SECONDS) return null;
      return parsed;
    } catch {
      // Not this secret (or not ours at all); try the next.
    }
  }
  return null;
}

/**
 * Where to go after signing in: a same-origin path, normalised, or "/".
 *
 * Refused unless every character is visible ASCII (a tab or newline the browser
 * would strip could turn "/\t/evil" into "//evil"), it starts with exactly one
 * "/", and resolving it against the app's origin stays on that origin. What is
 * kept is the normalised path and query, never the raw input.
 */
export function safeReturnTo(raw: unknown, appOrigin: string): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 512) return "/";
  if (!/^[\x21-\x7e]+$/.test(raw)) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";
  try {
    const url = new URL(raw, appOrigin);
    if (url.origin !== appOrigin) return "/";
    const path = `${url.pathname}${url.search}`;
    // "/.//host" normalises to "//host": always prefixed with the app's origin
    // here, so never a redirect elsewhere — but refused all the same, as the
    // one-leading-slash rule says.
    if (path.startsWith("//") || path.startsWith("/api/auth/")) return "/";
    return path;
  } catch {
    return "/";
  }
}
