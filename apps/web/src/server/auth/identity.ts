/**
 * THE identity seam of the web app: who a browser request is for.
 *
 * Everything in the web app that needs to know who the caller is asks here and
 * nowhere else — the API proxy, when it attaches the credential upstream; the
 * layout, when it prints who you are; `/api/auth/session`, when the Chrome
 * extension asks before saving. So what is displayed, what the extension says
 * it will save as, and what the API actually resolves cannot disagree.
 *
 * The same answer for both clients. The web UI and the extension are both
 * browser contexts presenting this server's session cookie (the extension's
 * requests carry it because it holds host permission for this server); the
 * only thing that differs between them is ADMISSION, which is
 * src/server/request-guard.ts. Nothing a client sends is ever taken as
 * identity: no header, no body field, no query parameter. The incoming
 * request's headers are not forwarded upstream at all — the proxy builds the
 * upstream request from `upstreamHeaders` here.
 *
 * The other half of the seam is the API's (services/api/app/api/identity.py),
 * which turns `upstreamHeaders` into a user. The two change together, and
 * nothing else does:
 *
 *   mode          who the request is for              upstream credential
 *   development   DEV_USER_EMAIL, for every request    X-Dev-User-Email
 *   dev-session   the signed v1 session cookie         X-Dev-User-Email
 *   entra         the signed v2 session cookie issued  Authorization: Bearer <assertion>
 *                 after a validated Microsoft sign-in  (./assertion.ts, minted per request)
 *
 * The API refuses X-Dev-User-Email under ENVIRONMENT=production and in its own
 * entra mode, and a development-mode API refuses a bearer assertion, so the
 * two tiers cannot silently disagree about which mode they are in.
 */

import { sessionLabel, type WebAuthMode } from "@easyapply/shared";

import { mintAssertion } from "./assertion";
import { readAuthConfig, type AuthConfig } from "./config";
import { normalizeEmail } from "./email";
import { newSessionId, signEntraSession, verifyEntraSession } from "./entra-session";
import type { ValidatedSignIn } from "./oidc";
import { signSession, verifySession, nowSeconds, toBase64Url } from "./session-token";

export const SESSION_COOKIE = "easyapply_session";

/**
 * The session cookie's name under real sign-in over https: the `__Host-`
 * prefix, which browsers accept only with Secure, Path=/ and no Domain — so a
 * page on a sibling subdomain cannot plant its own (valid) session on this
 * host, and no Domain-wide cookie can shadow it. Over plain http (a developer's
 * loopback, the local test stack) the prefix cannot be used, and the name is
 * the plain one.
 */
export const HOST_SESSION_COOKIE = "__Host-easyapply_session";

export function sessionCookieName(config: AuthConfig): string {
  return config.mode === "entra" && config.secureCookies ? HOST_SESSION_COOKIE : SESSION_COOKIE;
}

/** Read access to the request's cookies, whatever kind of request it is. */
export interface CookieReader {
  get(name: string): string | undefined;
}

export type Identity =
  | {
      status: "authenticated";
      mode: WebAuthMode;
      /** Display only, and possibly unknown under real sign-in. Never identity. */
      email: string | null;
      name: string | null;
      /** What the person is called on screen and in the extension ("Saving as …"). */
      label: string;
      /** The account's tag: see `WebSession.sessionTag`. */
      sessionTag: string;
      /**
       * Whether /internal is open to this person. Under real sign-in, only to
       * holders of the operator app role; in the development modes to everyone,
       * as it always was (the API's operator token is the boundary there).
       */
      operator: boolean;
      /**
       * How the API is told who this is. Built here, never copied from the
       * request — and a function, because an entra assertion is single-use and
       * only the proxy, when it forwards, should mint one.
       */
      upstreamHeaders: () => Promise<Record<string, string>>;
    }
  /** No usable session cookie: missing, forged, expired, or signed with another secret. */
  | { status: "signed_out"; mode: "dev-session" | "entra" }
  | { status: "misconfigured"; reason: string };

const encoder = new TextEncoder();

/**
 * The account tag: SHA-256 over the mode and the account's stable key, 22
 * base64url characters. Keyed to the ACCOUNT, not the session, so signing in
 * again as the same person (after an expiry, in another tab) keeps it and only
 * a different account changes it. Not a secret and not a credential: the proxy
 * only ever compares it with the tag of the session the request carries.
 */
export async function accountTag(mode: WebAuthMode, accountKey: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(`easyapply-account-tag-v1\n${mode}\n${accountKey}`));
  return toBase64Url(new Uint8Array(digest)).slice(0, 22);
}

export async function resolveIdentity(
  cookies: CookieReader,
  config: AuthConfig = readAuthConfig(),
): Promise<Identity> {
  switch (config.mode) {
    case "development": {
      // No cookie is read: every admitted request is the configured person.
      // That is the posture the admission rules were written for.
      const email = config.devUserEmail;
      return {
        status: "authenticated",
        mode: "development",
        email,
        name: null,
        label: sessionLabel({ email }),
        sessionTag: await accountTag("development", normalizeEmail(email)),
        operator: true,
        upstreamHeaders: async () => ({ "X-Dev-User-Email": email }),
      };
    }

    case "dev-session": {
      const claims = await verifySession(cookies.get(SESSION_COOKIE), config.secret);
      if (!claims) return { status: "signed_out", mode: "dev-session" };
      return {
        status: "authenticated",
        mode: "dev-session",
        email: claims.sub,
        name: null,
        label: claims.sub,
        sessionTag: await accountTag("dev-session", claims.sub),
        operator: true,
        upstreamHeaders: async () => ({ "X-Dev-User-Email": claims.sub }),
      };
    }

    case "entra": {
      const secrets = config.previousSecret ? [config.secret, config.previousSecret] : [config.secret];
      const claims = await verifyEntraSession(cookies.get(sessionCookieName(config)), secrets);
      // A session from another tenant, issuer configuration or application
      // (client id) is not this deployment's — even one signed with the same
      // secret — and one older than the CURRENT lifetime has ended: a
      // shortened SESSION_MAX_AGE_SECONDS takes effect on every session at once.
      if (
        !claims ||
        claims.iss !== config.entra.issuer ||
        claims.tid !== config.entra.tenantId ||
        claims.aud !== config.entra.clientId ||
        nowSeconds() - claims.iat > config.maxAgeSeconds
      ) {
        return { status: "signed_out", mode: "entra" };
      }
      return {
        status: "authenticated",
        mode: "entra",
        email: claims.email,
        name: claims.name,
        label: sessionLabel(claims),
        sessionTag: await accountTag("entra", `${claims.tid}:${claims.oid}`),
        operator: claims.op,
        upstreamHeaders: async () => ({
          Authorization: `Bearer ${await mintAssertion(claims, config.entra, config.assertionSecret)}`,
        }),
      };
    }

    case "misconfigured":
      return { status: "misconfigured", reason: config.reason };
  }
}

/** A new development session for `email`, valid for the configured lifetime. */
export function issueSession(email: string, config: Extract<AuthConfig, { mode: "dev-session" }>): Promise<string> {
  const iat = nowSeconds();
  return signSession({ sub: email, iat, exp: iat + config.maxAgeSeconds }, config.secret);
}

/** A new real session, from a sign-in the callback has just validated. */
export function issueEntraSession(
  signIn: ValidatedSignIn,
  config: Extract<AuthConfig, { mode: "entra" }>,
  now: number = nowSeconds(),
): Promise<string> {
  return signEntraSession(
    {
      typ: "entra",
      iss: signIn.issuer,
      aud: config.entra.clientId,
      tid: signIn.tenantId,
      oid: signIn.objectId,
      email: signIn.email,
      name: signIn.name,
      op: signIn.roles.includes(config.entra.operatorRole),
      sid: newSessionId(),
      iat: now,
      exp: now + config.maxAgeSeconds,
    },
    config.secret,
  );
}

/**
 * The Set-Cookie value for a session.
 *
 *   HttpOnly     no JavaScript — the web UI's or the extension's — can read it.
 *   SameSite=Lax not sent on cross-site subrequests or cross-site POSTs; sent
 *                on a top-level link from elsewhere, so arriving from an email
 *                or the extension's "Open in EasyApply" is still signed in.
 *                Not the CSRF defence (admission is); a second layer.
 *   Secure       whenever this server is reached over https (always, under
 *                real sign-in: APP_ORIGIN must be https unless loopback).
 *   Path=/       the whole app; no Domain, so it is host-only.
 */
export function sessionCookie(token: string, maxAgeSeconds: number, secure: boolean, name: string = SESSION_COOKIE): string {
  return [
    `${name}=${token}`,
    "Path=/",
    `Max-Age=${maxAgeSeconds}`,
    "HttpOnly",
    "SameSite=Lax",
    ...(secure ? ["Secure"] : []),
  ].join("; ");
}

export function clearedSessionCookie(secure: boolean, name: string = SESSION_COOKIE): string {
  return [`${name}=`, "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax", ...(secure ? ["Secure"] : [])].join(
    "; ",
  );
}

/** Whether the browser reached this server over https (directly or through a proxy that says so). */
export function isHttps(request: Request): boolean {
  if (new URL(request.url).protocol === "https:") return true;
  return request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim().toLowerCase() === "https";
}
