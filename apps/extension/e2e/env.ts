/**
 * Where the browser suite's extension build lives and what it saves to.
 *
 * By default the suite runs against a stub of the web app (e2e/stub-server.ts)
 * on STUB_PORT, which records every request exactly as Chrome sent it. Set
 * EXTENSION_E2E_WEB_URL to point the same suite at a REAL EasyApply web app
 * instead — its proxy started with PROXY_ALLOWED_EXTENSION_IDS naming this
 * extension, and an API and database behind it that nobody minds a test
 * writing to. Never the development stack: the suite saves jobs.
 */

import { createHash, createHmac, randomBytes } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

export const STUB_PORT = 3397;
export const REAL_WEB_URL = process.env.EXTENSION_E2E_WEB_URL?.replace(/\/+$/, "") || null;
/**
 * The API behind that web app, for SETUP only: the real-stack specs seed each
 * test account's confirmed profile through it directly (X-Dev-User-Email, as
 * any development client may), because building a profile is not what this
 * suite is about. Everything the extension does still goes through the web app.
 */
export const REAL_API_URL = process.env.EXTENSION_E2E_API_URL?.replace(/\/+$/, "") || null;
export const WEB_URL = REAL_WEB_URL ?? `http://127.0.0.1:${STUB_PORT}`;
export const BUILD_DIR = resolve(HERE, "../.artifacts/e2e-extension");
export const FIXTURES = resolve(HERE, "../test/fixtures");
export const EXTENSION_ID = "jjhciekcmhmngciccfgignmmlgjknakk";

/**
 * Real sign-in (`node e2e/real-stack.mjs --entra`): the web app and the API in
 * AUTH_MODE=entra, signing in through a local OpenID provider shaped like
 * Entra External ID (apps/web/e2e/support/mock-entra.mjs, "another account"
 * enabled so each run's people exist). Null otherwise.
 */
export const REAL_ENTRA =
  process.env.EXTENSION_E2E_AUTH === "entra"
    ? {
        tenantId: process.env.EXTENSION_E2E_ENTRA_TENANT_ID ?? "",
        issuer: process.env.EXTENSION_E2E_ENTRA_ISSUER ?? "",
        assertionSecret: process.env.EXTENSION_E2E_ASSERTION_SECRET ?? "",
      }
    : null;

/** The provider's object id for a made-up account — the same function as `oidForEmail` in mock-entra.mjs. */
export function oidForEmail(email: string): string {
  const hex = createHash("sha256").update(`mock-entra-oid:${email.trim().toLowerCase()}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

/**
 * SETUP under real sign-in: the header the web app would send the API for this
 * person — a 60-second assertion signed with the run's test secret. The API
 * refuses X-Dev-User-Email in entra mode, so seeding a profile directly needs
 * this instead. Everything the extension does still goes through the web app.
 */
export function entraAuthorization(email: string): string {
  if (!REAL_ENTRA) throw new Error("not an entra run");
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    iss: "easyapply-web",
    aud: "easyapply-api",
    sub: oidForEmail(email),
    idp: REAL_ENTRA.issuer,
    tid: REAL_ENTRA.tenantId,
    email,
    iat: now,
    nbf: now,
    exp: now + 60,
    jti: randomBytes(16).toString("base64url"),
  };
  const signingInput = `${encode({ alg: "HS256", typ: "JWT" })}.${encode(payload)}`;
  const signature = createHmac("sha256", REAL_ENTRA.assertionSecret).update(signingInput).digest("base64url");
  return `Bearer ${signingInput}.${signature}`;
}
