/**
 * How this web server establishes identity: `AUTH_MODE` and what it needs.
 *
 * Read per request (like PROXY_ALLOWED_HOSTS), so what a request is answered
 * with is exactly what the process environment says now.
 *
 *   development  (default) everyone is DEV_USER_EMAIL. A developer's machine.
 *   dev-session  a per-browser session from a development sign-in form: any
 *                address, no password. NOT authentication — the test suites'
 *                multi-user mode.
 *   entra        real sign-in: Microsoft Entra External ID, authorization code
 *                with PKCE, the ID token validated here, an EasyApply session
 *                cookie issued, and the API told who the person is by a short
 *                signed assertion (./assertion.ts). The production mode.
 *
 * A closed set, and it fails CLOSED: an unknown mode, or a mode without
 * everything it needs, is `misconfigured`, and every route that needs an
 * identity refuses (500 `auth_misconfigured`) rather than falling back to the
 * development identity. A typo must not turn real sign-in back into "everyone
 * is DEV_USER_EMAIL". The reason names settings, never their values.
 */

import type { WebAuthMode } from "@easyapply/shared";

import { isAllowedHostname } from "@/server/request-guard";

/** An HMAC-SHA256 key should be at least as long as its output. */
export const MIN_SESSION_SECRET_LENGTH = 32;
export const DEFAULT_SESSION_MAX_AGE_SECONDS = 12 * 60 * 60;
/** Real sign-in lasts a working day; signing in again is cheap, a stolen cookie is not. */
export const DEFAULT_ENTRA_SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;
const MAX_SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;
/** The Entra app role that may use the operator pages (/internal). */
export const DEFAULT_OPERATOR_ROLE = "EasyApply.Operator";

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LOOPBACK_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

export interface EntraConfig {
  tenantId: string;
  /** Where discovery is fetched: `<authority>/.well-known/openid-configuration`. */
  authority: string;
  /** The `iss` every ID token must carry — pinned, not taken from discovery (see oidc.ts). */
  issuer: string;
  clientId: string;
  /** Server-side only. Never in a bundle, a response, a log or the extension. */
  clientSecret: string;
  redirectUri: string;
  postLogoutRedirectUri: string;
  operatorRole: string;
}

export type AuthConfig =
  | { mode: "development"; devUserEmail: string }
  | { mode: "dev-session"; secret: string; maxAgeSeconds: number; suggestedEmail: string | null }
  | {
      mode: "entra";
      entra: EntraConfig;
      /** Signs sessions and seals sign-in transactions. */
      secret: string;
      /** Still accepted for VERIFYING during a rotation; never used to sign. */
      previousSecret: string | null;
      /** Signs the web -> API identity assertion. Never the session secret. */
      assertionSecret: string;
      maxAgeSeconds: number;
      /** The public origin sign-in returns to; cookies are Secure when it is https. */
      appOrigin: string;
      secureCookies: boolean;
    }
  | { mode: "misconfigured"; reason: string };

function maxAge(raw: string | undefined, fallback: number): number | string {
  const value = raw?.trim() ? Number(raw.trim()) : fallback;
  if (!Number.isInteger(value) || value < 60 || value > MAX_SESSION_MAX_AGE_SECONDS) {
    return `SESSION_MAX_AGE_SECONDS must be a whole number of seconds from 60 to ${MAX_SESSION_MAX_AGE_SECONDS}.`;
  }
  return value;
}

/** An https URL, or http on a loopback host (the local test provider). Null if neither. */
function secureUrl(raw: string | undefined): URL | null {
  if (!raw?.trim()) return null;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.username || url.password || url.search || url.hash) return null;
  if (url.protocol === "https:") return url;
  if (url.protocol === "http:" && LOOPBACK_HOSTNAMES.has(url.hostname)) return url;
  return null;
}

function readEntra(env: Record<string, string | undefined>): AuthConfig {
  const problems: string[] = [];

  const tenantId = env.ENTRA_TENANT_ID?.trim() ?? "";
  if (!GUID.test(tenantId)) problems.push("ENTRA_TENANT_ID must be the tenant's GUID");

  const authority = secureUrl(env.ENTRA_AUTHORITY);
  if (!authority) problems.push("ENTRA_AUTHORITY must be the tenant's https authority (…ciamlogin.com/<tenant-id>/v2.0)");

  const tenant = tenantId.toLowerCase();
  // The form External ID's tokens carry: the tenant id, lower-cased, as host and path.
  const issuerRaw = env.ENTRA_ISSUER?.trim() || (GUID.test(tenantId) ? `https://${tenant}.ciamlogin.com/${tenant}/v2.0` : "");
  const issuer = secureUrl(issuerRaw);
  if (!issuer) problems.push("ENTRA_ISSUER must be the tenant's https token issuer");
  else if (tenantId && !issuerRaw.toLowerCase().includes(tenantId.toLowerCase())) problems.push("ENTRA_ISSUER must name ENTRA_TENANT_ID");

  const clientId = env.ENTRA_CLIENT_ID?.trim() ?? "";
  if (!GUID.test(clientId)) problems.push("ENTRA_CLIENT_ID must be the application (client) id");
  const clientSecret = env.ENTRA_CLIENT_SECRET ?? "";
  if (!clientSecret.trim()) problems.push("ENTRA_CLIENT_SECRET is required");

  const origin = secureUrl(env.APP_ORIGIN);
  if (!origin || origin.pathname !== "/") {
    problems.push("APP_ORIGIN must be this app's public origin (https unless loopback), with no path");
  } else if (!isAllowedHostname(origin.hostname, env.PROXY_ALLOWED_HOSTS)) {
    // Microsoft returns the browser to APP_ORIGIN; a host this server refuses
    // would answer the callback 403 and no sign-in could ever complete.
    problems.push("APP_ORIGIN's host must be loopback or listed in PROXY_ALLOWED_HOSTS");
  }

  const secret = env.SESSION_SECRET ?? "";
  if (secret.length < MIN_SESSION_SECRET_LENGTH) problems.push(`SESSION_SECRET must be at least ${MIN_SESSION_SECRET_LENGTH} characters`);
  const previous = env.SESSION_SECRET_PREVIOUS ?? "";
  if (previous && previous.length < MIN_SESSION_SECRET_LENGTH) problems.push(`SESSION_SECRET_PREVIOUS, when set, must be at least ${MIN_SESSION_SECRET_LENGTH} characters`);
  const assertionSecret = env.IDENTITY_ASSERTION_SECRET ?? "";
  if (assertionSecret.length < MIN_SESSION_SECRET_LENGTH) {
    problems.push(`IDENTITY_ASSERTION_SECRET must be at least ${MIN_SESSION_SECRET_LENGTH} characters`);
  } else if (assertionSecret === secret || assertionSecret === previous) {
    problems.push("IDENTITY_ASSERTION_SECRET must not be a session secret");
  }

  const lifetime = maxAge(env.SESSION_MAX_AGE_SECONDS, DEFAULT_ENTRA_SESSION_MAX_AGE_SECONDS);
  if (typeof lifetime === "string") problems.push(lifetime);

  const operatorRole = env.ENTRA_OPERATOR_ROLE?.trim() || DEFAULT_OPERATOR_ROLE;

  if (problems.length > 0 || !authority || !issuer || !origin || typeof lifetime === "string") {
    return { mode: "misconfigured", reason: `AUTH_MODE=entra: ${problems.join("; ")}.` };
  }

  const appOrigin = origin.origin;
  return {
    mode: "entra",
    entra: {
      tenantId: tenant,
      authority: authority.href.replace(/\/+$/, ""),
      issuer: issuerRaw,
      clientId: clientId.toLowerCase(),
      clientSecret,
      redirectUri: `${appOrigin}/api/auth/callback`,
      postLogoutRedirectUri: `${appOrigin}/signed-out`,
      operatorRole,
    },
    secret,
    previousSecret: previous || null,
    assertionSecret,
    maxAgeSeconds: lifetime,
    appOrigin,
    secureCookies: origin.protocol === "https:",
  };
}

export function readAuthConfig(env: Record<string, string | undefined> = process.env): AuthConfig {
  const mode = (env.AUTH_MODE ?? "").trim().toLowerCase() || "development";

  if (mode === "development") {
    // Exactly what the proxy has always sent: the value as configured, with
    // the API's seam normalising it (services/api/app/api/dev_identity.py).
    return { mode, devUserEmail: env.DEV_USER_EMAIL ?? "dev@easyapply.local" };
  }

  if (mode === "dev-session") {
    const secret = env.SESSION_SECRET ?? "";
    if (secret.length < MIN_SESSION_SECRET_LENGTH) {
      return {
        mode: "misconfigured",
        reason: `AUTH_MODE=dev-session needs SESSION_SECRET of at least ${MIN_SESSION_SECRET_LENGTH} characters.`,
      };
    }
    const lifetime = maxAge(env.SESSION_MAX_AGE_SECONDS, DEFAULT_SESSION_MAX_AGE_SECONDS);
    if (typeof lifetime === "string") return { mode: "misconfigured", reason: lifetime };
    return { mode, secret, maxAgeSeconds: lifetime, suggestedEmail: env.DEV_USER_EMAIL?.trim() || null };
  }

  if (mode === "entra") return readEntra(env);

  return {
    mode: "misconfigured",
    reason: `AUTH_MODE must be "development", "dev-session" or "entra", not ${JSON.stringify(env.AUTH_MODE)}.`,
  };
}

export function isWebAuthMode(value: string): value is WebAuthMode {
  return value === "development" || value === "dev-session" || value === "entra";
}
