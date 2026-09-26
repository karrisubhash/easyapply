/**
 * A fake Microsoft Entra External ID tenant for unit tests: msw handlers at the
 * network boundary and a real RS256 key, so what is exercised is the real
 * discovery, token redemption and ID token validation — jose included.
 *
 * Shaped like External ID: discovery on the tenant-NAME host, tokens issued by
 * the tenant-ID host (see src/server/auth/oidc.ts).
 */

import { exportJWK, generateKeyPair, SignJWT, type JWTPayload } from "jose";
import { http, HttpResponse } from "msw";

import { server } from "./server";

export const TENANT = "11111111-2222-4333-8444-555555555555";
export const CLIENT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
export const CLIENT_SECRET = "client-secret-for-tests-only";
export const OID_A = "0a0a0a0a-1111-4222-8333-444444444444";
export const OID_B = "0b0b0b0b-1111-4222-8333-444444444444";
export const HOST = "https://easyapply-test.ciamlogin.com";
export const AUTHORITY = `${HOST}/${TENANT}/v2.0`;
export const ISSUER = `https://${TENANT}.ciamlogin.com/${TENANT}/v2.0`;
export const ENDPOINTS = {
  authorize: `${HOST}/${TENANT}/oauth2/v2.0/authorize`,
  token: `${HOST}/${TENANT}/oauth2/v2.0/token`,
  jwks: `${HOST}/${TENANT}/discovery/v2.0/keys`,
  logout: `${HOST}/${TENANT}/oauth2/v2.0/logout`,
};
export const SESSION_SECRET = "an-entra-session-secret-of-32+characters";
export const ASSERTION_SECRET = "an-identity-assertion-secret-of-32+chars";
export const APP_ORIGIN = "http://localhost:3000";
/** What the fake token endpoint returns beside the ID token: nothing may ever keep either. */
export const ACCESS_TOKEN = "ACCESS-TOKEN-NEVER-KEPT";
export const REFRESH_TOKEN = "REFRESH-TOKEN-NEVER-KEPT";

export const ENTRA_ENV: Record<string, string> = {
  AUTH_MODE: "entra",
  ENTRA_TENANT_ID: TENANT,
  ENTRA_AUTHORITY: AUTHORITY,
  ENTRA_CLIENT_ID: CLIENT_ID,
  ENTRA_CLIENT_SECRET: CLIENT_SECRET,
  APP_ORIGIN,
  SESSION_SECRET,
  IDENTITY_ASSERTION_SECRET: ASSERTION_SECRET,
};

export function stubEntraEnv(overrides: Record<string, string> = {}): void {
  for (const [name, value] of Object.entries({ ...ENTRA_ENV, ...overrides })) vi.stubEnv(name, value);
}

export interface TokenRequest {
  form: URLSearchParams;
}

export interface FakeTenant {
  /** An ID token signed by the tenant's key, with sensible claims unless overridden. */
  idToken(claims?: JWTPayload & Record<string, unknown>, options?: { kid?: string; iatOffset?: number }): Promise<string>;
  /** What the token endpoint answers for a code: an ID token, or an error body. */
  codes: Map<string, { idToken: string } | { status: number; body: Record<string, unknown> }>;
  tokenRequests: TokenRequest[];
  discovery: Record<string, unknown>;
  /** Replace the discovery document's fields for this tenant. */
  setDiscovery(fields: Record<string, unknown>): void;
  keySet: { keys: unknown[] };
}

/**
 * One RSA key per test process. Generating a 2048-bit key takes a noticeable
 * fraction of a second, and a test that makes several tenants would otherwise
 * spend its time budget on key generation. Every tenant publishing the same key
 * also means a key set cached from one tenant still verifies the next.
 */
let sharedKeyPair: ReturnType<typeof generateKeyPair> | null = null;

export async function fakeTenant(): Promise<FakeTenant> {
  sharedKeyPair ??= generateKeyPair("RS256", { extractable: true });
  const { privateKey, publicKey } = await sharedKeyPair;
  const jwk = { ...(await exportJWK(publicKey)), kid: "key-1", alg: "RS256", use: "sig" };
  const codes: FakeTenant["codes"] = new Map();
  const tokenRequests: TokenRequest[] = [];
  let discovery: Record<string, unknown> = {
    issuer: ISSUER,
    authorization_endpoint: ENDPOINTS.authorize,
    token_endpoint: ENDPOINTS.token,
    jwks_uri: ENDPOINTS.jwks,
    end_session_endpoint: ENDPOINTS.logout,
  };
  const keySet = { keys: [jwk] as unknown[] };

  server.use(
    http.get(`${AUTHORITY}/.well-known/openid-configuration`, () => HttpResponse.json(discovery)),
    http.get(ENDPOINTS.jwks, () => HttpResponse.json(keySet)),
    http.post(ENDPOINTS.token, async ({ request }) => {
      const form = new URLSearchParams(await request.text());
      tokenRequests.push({ form });
      const code = form.get("code") ?? "";
      const answer = codes.get(code);
      codes.delete(code); // single use, like the real thing
      if (!answer) return HttpResponse.json({ error: "invalid_grant", error_codes: [54005] }, { status: 400 });
      if ("idToken" in answer) return HttpResponse.json({ id_token: answer.idToken, access_token: ACCESS_TOKEN, refresh_token: REFRESH_TOKEN, token_type: "Bearer" });
      return HttpResponse.json(answer.body, { status: answer.status });
    }),
  );

  return {
    codes,
    tokenRequests,
    get discovery() {
      return discovery;
    },
    setDiscovery(fields) {
      discovery = { ...discovery, ...fields };
    },
    keySet,
    async idToken(claims = {}, options = {}) {
      const now = Math.floor(Date.now() / 1000) + (options.iatOffset ?? 0);
      return new SignJWT({
        oid: OID_A,
        tid: TENANT,
        sub: "pairwise-subject",
        nonce: "NONCE",
        email: "ada@example.test",
        name: "Ada Lovelace",
        ver: "2.0",
        ...claims,
      })
        .setProtectedHeader({ alg: "RS256", kid: options.kid ?? "key-1", typ: "JWT" })
        .setIssuer(typeof claims.iss === "string" ? claims.iss : ISSUER)
        .setAudience((claims.aud as string | string[] | undefined) ?? CLIENT_ID)
        .setIssuedAt(now)
        .setExpirationTime(now + 3600)
        .sign(privateKey);
    },
  };
}
