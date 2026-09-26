// @vitest-environment node

/**
 * Real sign-in, module by module: the configuration it refuses, the session it
 * issues, the transaction that survives the round trip to Microsoft, the
 * validation of what Microsoft sends back, and the assertion the API is told.
 */

import { jwtVerify } from "jose";
import { http, HttpResponse } from "msw";

import {
  APP_ORIGIN,
  ASSERTION_SECRET,
  AUTHORITY,
  CLIENT_ID,
  ENDPOINTS,
  ENTRA_ENV,
  fakeTenant,
  ISSUER,
  OID_A,
  SESSION_SECRET,
  TENANT,
} from "@/test/entra";
import { server } from "@/test/server";

import { ASSERTION_AUDIENCE, ASSERTION_ISSUER, mintAssertion } from "./assertion";
import { readAuthConfig, type AuthConfig } from "./config";
import { newSessionId, signEntraSession, verifyEntraSession, type EntraSessionClaims } from "./entra-session";
import { accountTag, issueEntraSession, resolveIdentity, SESSION_COOKIE } from "./identity";
import { authorizationUrl, discover, displayText, exchangeCode, logoutUrl, resetOidcCaches, SignInError, validateIdToken } from "./oidc";
import { nowSeconds, signSession } from "./session-token";
import { openTransaction, pkceChallenge, safeReturnTo, sealTransaction, transactionCookieName } from "./transaction";

type Entra = Extract<AuthConfig, { mode: "entra" }>;

function entraConfig(overrides: Record<string, string | undefined> = {}): Entra {
  const config = readAuthConfig({ ...ENTRA_ENV, ...overrides });
  if (config.mode !== "entra") throw new Error(`not entra: ${JSON.stringify(config)}`);
  return config;
}

function jar(values: Record<string, string> = {}) {
  return { get: (name: string) => values[name] };
}

function claims(overrides: Partial<EntraSessionClaims> = {}): EntraSessionClaims {
  const iat = nowSeconds();
  return {
    typ: "entra",
    iss: ISSUER,
    aud: CLIENT_ID,
    tid: TENANT,
    oid: OID_A,
    email: "ada@example.test",
    name: "Ada Lovelace",
    op: false,
    sid: newSessionId(),
    iat,
    exp: iat + 3600,
    ...overrides,
  };
}

beforeEach(() => {
  resetOidcCaches();
});

// --- configuration ---------------------------------------------------------------------------

describe("AUTH_MODE=entra configuration", () => {
  it("reads a complete configuration, with the External ID defaults", () => {
    const config = entraConfig();
    expect(config.entra).toEqual({
      tenantId: TENANT,
      authority: AUTHORITY,
      issuer: ISSUER,
      clientId: CLIENT_ID,
      clientSecret: "client-secret-for-tests-only",
      redirectUri: `${APP_ORIGIN}/api/auth/callback`,
      postLogoutRedirectUri: `${APP_ORIGIN}/signed-out`,
      operatorRole: "EasyApply.Operator",
    });
    expect(config.maxAgeSeconds).toBe(8 * 60 * 60);
    expect(config.previousSecret).toBeNull();
    expect(config.secureCookies).toBe(false);
  });

  it("builds the default issuer from the tenant id in lower case, as External ID's tokens carry it", () => {
    const upper = TENANT.toUpperCase();
    const config = entraConfig({ ENTRA_TENANT_ID: upper });
    expect(config.entra.issuer).toBe(ISSUER);
    expect(config.entra.tenantId).toBe(TENANT);
  });

  it("marks cookies Secure for an https origin that is an allowed host", () => {
    const config = entraConfig({ APP_ORIGIN: "https://app.easyapply.example", PROXY_ALLOWED_HOSTS: "app.easyapply.example" });
    expect(config.secureCookies).toBe(true);
    expect(config.entra.redirectUri).toBe("https://app.easyapply.example/api/auth/callback");
  });

  it.each([
    ["no tenant", { ENTRA_TENANT_ID: "" }, "ENTRA_TENANT_ID"],
    ["a tenant that is a name, not a GUID", { ENTRA_TENANT_ID: "easyapply.onmicrosoft.com" }, "ENTRA_TENANT_ID"],
    ["an http authority off loopback", { ENTRA_AUTHORITY: `http://easyapply-test.ciamlogin.com/${TENANT}/v2.0` }, "ENTRA_AUTHORITY"],
    ["an authority with a query", { ENTRA_AUTHORITY: `${AUTHORITY}?p=1` }, "ENTRA_AUTHORITY"],
    ["an issuer for another tenant", { ENTRA_ISSUER: "https://99999999-2222-4333-8444-555555555555.ciamlogin.com/x/v2.0" }, "ENTRA_ISSUER"],
    ["an http issuer", { ENTRA_ISSUER: `http://${TENANT}.ciamlogin.com/${TENANT}/v2.0` }, "ENTRA_ISSUER"],
    ["no client id", { ENTRA_CLIENT_ID: "" }, "ENTRA_CLIENT_ID"],
    ["no client secret", { ENTRA_CLIENT_SECRET: " " }, "ENTRA_CLIENT_SECRET"],
    ["an origin with a path", { APP_ORIGIN: "http://localhost:3000/app" }, "APP_ORIGIN"],
    ["an http origin off loopback", { APP_ORIGIN: "http://app.easyapply.example", PROXY_ALLOWED_HOSTS: "app.easyapply.example" }, "APP_ORIGIN"],
    ["an origin this server does not answer for", { APP_ORIGIN: "https://app.easyapply.example" }, "PROXY_ALLOWED_HOSTS"],
    ["a short session secret", { SESSION_SECRET: "short" }, "SESSION_SECRET"],
    ["a short previous secret", { SESSION_SECRET_PREVIOUS: "short" }, "SESSION_SECRET_PREVIOUS"],
    ["a short assertion secret", { IDENTITY_ASSERTION_SECRET: "short" }, "IDENTITY_ASSERTION_SECRET"],
    ["the assertion secret reused as the session secret", { IDENTITY_ASSERTION_SECRET: SESSION_SECRET }, "IDENTITY_ASSERTION_SECRET"],
    ["an unusable lifetime", { SESSION_MAX_AGE_SECONDS: "forever" }, "SESSION_MAX_AGE_SECONDS"],
  ])("is misconfigured with %s — naming the setting, never a value", (_what, overrides, setting) => {
    const config = readAuthConfig({ ...ENTRA_ENV, ...overrides });
    expect(config.mode).toBe("misconfigured");
    if (config.mode !== "misconfigured") return;
    expect(config.reason).toContain(setting);
    for (const secret of [SESSION_SECRET, ASSERTION_SECRET, "client-secret-for-tests-only"]) {
      expect(config.reason).not.toContain(secret);
    }
  });

  it("never falls back to the development identity", async () => {
    const identity = await resolveIdentity(jar(), readAuthConfig({ ...ENTRA_ENV, ENTRA_CLIENT_SECRET: "" }));
    expect(identity.status).toBe("misconfigured");
  });
});

// --- the session ------------------------------------------------------------------------------

describe("the entra session (v2)", () => {
  it("round-trips, and verifies under the previous secret during a rotation", async () => {
    const token = await signEntraSession(claims(), SESSION_SECRET);
    expect(token).toMatch(/^v2\./);
    expect(await verifyEntraSession(token, [SESSION_SECRET])).toMatchObject({ oid: OID_A });
    expect(await verifyEntraSession(token, ["a-brand-new-secret-of-32+characters!", SESSION_SECRET])).toMatchObject({ oid: OID_A });
    expect(await verifyEntraSession(token, ["a-brand-new-secret-of-32+characters!"])).toBeNull();
  });

  it("refuses an edited payload, a foreign signature, an expired or future one", async () => {
    const token = await signEntraSession(claims(), SESSION_SECRET);
    const [version, payload, signature] = token.split(".");
    const edited = JSON.parse(Buffer.from(payload!, "base64url").toString());
    edited.oid = "0b0b0b0b-1111-4222-8333-444444444444";
    const forged = `${version}.${Buffer.from(JSON.stringify(edited)).toString("base64url")}.${signature}`;
    expect(await verifyEntraSession(forged, [SESSION_SECRET])).toBeNull();
    expect(await verifyEntraSession(await signEntraSession(claims(), "another-secret-of-at-least-32-chars"), [SESSION_SECRET])).toBeNull();
    const past = nowSeconds() - 7200;
    expect(await verifyEntraSession(await signEntraSession(claims({ iat: past, exp: past + 60 }), SESSION_SECRET), [SESSION_SECRET])).toBeNull();
    const future = nowSeconds() + 3600;
    expect(await verifyEntraSession(await signEntraSession(claims({ iat: future, exp: future + 60 }), SESSION_SECRET), [SESSION_SECRET])).toBeNull();
  });

  it("is not a development session, and a development session is not it — even under one secret", async () => {
    const v1 = await signSession({ sub: "ada@example.test", iat: nowSeconds(), exp: nowSeconds() + 60 }, SESSION_SECRET);
    expect(await verifyEntraSession(v1, [SESSION_SECRET])).toBeNull();
    const config = entraConfig();
    expect(await resolveIdentity(jar({ [SESSION_COOKIE]: v1 }), config)).toEqual({ status: "signed_out", mode: "entra" });
  });

  it("refuses claims of the wrong shape: extra keys, a non-GUID oid, the wrong typ", async () => {
    for (const bad of [
      { ...claims(), extra: 1 },
      claims({ oid: "not-a-guid" }),
      { ...claims(), typ: "dev" },
      claims({ op: "yes" as unknown as boolean }),
    ]) {
      expect(await verifyEntraSession(await signEntraSession(bad as EntraSessionClaims, SESSION_SECRET), [SESSION_SECRET])).toBeNull();
    }
  });

  it("is signed out when it belongs to another tenant, issuer or application, or outlives the current lifetime", async () => {
    const config = entraConfig();
    for (const other of [
      claims({ tid: "99999999-2222-4333-8444-555555555555" }),
      // Another deployment's session, even under the same secret and tenant.
      claims({ aud: "99999999-bbbb-4ccc-8ddd-eeeeeeeeeeee" }),
      claims({ iss: "https://elsewhere.example/v2.0" }),
      claims({ iat: nowSeconds() - 9 * 3600, exp: nowSeconds() + 3600 }),
    ]) {
      const token = await signEntraSession(other, SESSION_SECRET);
      expect(await resolveIdentity(jar({ [SESSION_COOKIE]: token }), config)).toEqual({ status: "signed_out", mode: "entra" });
    }
  });

  it("resolves to the account, with its label, tag and operator flag, and mints the API's assertion", async () => {
    const config = entraConfig();
    const token = await signEntraSession(claims({ op: true }), SESSION_SECRET);
    const identity = await resolveIdentity(jar({ [SESSION_COOKIE]: token }), config);
    if (identity.status !== "authenticated") throw new Error("identity");
    expect(identity).toMatchObject({ mode: "entra", email: "ada@example.test", name: "Ada Lovelace", label: "ada@example.test", operator: true });
    expect(identity.sessionTag).toBe(await accountTag("entra", `${TENANT}:${OID_A}`));

    const headers = await identity.upstreamHeaders();
    expect(Object.keys(headers)).toEqual(["Authorization"]);
    const assertion = headers.Authorization!.replace(/^Bearer /, "");
    const { payload, protectedHeader } = await jwtVerify(assertion, new TextEncoder().encode(ASSERTION_SECRET), {
      issuer: ASSERTION_ISSUER,
      audience: ASSERTION_AUDIENCE,
      algorithms: ["HS256"],
    });
    expect(protectedHeader.alg).toBe("HS256");
    expect(payload).toMatchObject({ sub: OID_A, idp: ISSUER, tid: TENANT, email: "ada@example.test", roles: ["EasyApply.Operator"] });
    expect(payload.exp! - payload.iat!).toBe(60);
    expect(payload.jti).toMatch(/^[A-Za-z0-9_-]{22}$/);
    // Each request its own assertion: the API accepts each jti once.
    expect((await identity.upstreamHeaders()).Authorization).not.toBe(headers.Authorization);
  });

  it("an account with neither email nor name is still signed in, and says so plainly", async () => {
    const config = entraConfig();
    const token = await signEntraSession(claims({ email: null, name: null }), SESSION_SECRET);
    const identity = await resolveIdentity(jar({ [SESSION_COOKIE]: token }), config);
    expect(identity).toMatchObject({ status: "authenticated", label: "your EasyApply account", operator: false });
    if (identity.status !== "authenticated") return;
    const assertion = (await identity.upstreamHeaders()).Authorization!.replace(/^Bearer /, "");
    const { payload } = await jwtVerify(assertion, new TextEncoder().encode(ASSERTION_SECRET));
    expect(payload).not.toHaveProperty("email");
    expect(payload).not.toHaveProperty("name");
    expect(payload).not.toHaveProperty("roles");
  });

  it("the same person signing in again keeps the account tag; another person does not", async () => {
    const config = entraConfig();
    const tagOf = async (c: EntraSessionClaims) => {
      const identity = await resolveIdentity(jar({ [SESSION_COOKIE]: await signEntraSession(c, SESSION_SECRET) }), config);
      return identity.status === "authenticated" ? identity.sessionTag : null;
    };
    const first = await tagOf(claims());
    expect(await tagOf(claims({ email: "renamed@example.test" }))).toBe(first);
    expect(await tagOf(claims({ oid: "0b0b0b0b-1111-4222-8333-444444444444", email: "ada@example.test" }))).not.toBe(first);
  });
});

// --- the transaction -------------------------------------------------------------------------

describe("the sign-in transaction", () => {
  const transaction = () => ({ state: "S".repeat(43), nonce: "N".repeat(43), verifier: "V".repeat(43), returnTo: "/jobs", iat: nowSeconds() });

  it("is sealed: opened only with the secret, unreadable and uneditable without it", async () => {
    const sealed = await sealTransaction(transaction(), SESSION_SECRET);
    expect(sealed).not.toContain("SSSS");
    expect(await openTransaction(sealed, [SESSION_SECRET])).toMatchObject({ returnTo: "/jobs" });
    expect(await openTransaction(sealed, ["another-secret-of-at-least-32-chars"])).toBeNull();
    const bytes = Buffer.from(sealed, "base64url");
    bytes[bytes.length - 1] ^= 1;
    expect(await openTransaction(bytes.toString("base64url"), [SESSION_SECRET])).toBeNull();
    expect(await openTransaction("garbage", [SESSION_SECRET])).toBeNull();
    expect(await openTransaction(undefined, [SESSION_SECRET])).toBeNull();
  });

  it("lasts ten minutes", async () => {
    const sealed = await sealTransaction(transaction(), SESSION_SECRET);
    expect(await openTransaction(sealed, [SESSION_SECRET], nowSeconds() + 599)).not.toBeNull();
    expect(await openTransaction(sealed, [SESSION_SECRET], nowSeconds() + 601)).toBeNull();
  });

  it("is named after its state, so two tabs' sign-ins do not collide", async () => {
    const a = await transactionCookieName("state-a-0123456789abcdef");
    const b = await transactionCookieName("state-b-0123456789abcdef");
    expect(a).toMatch(/^easyapply_oidc_[A-Za-z0-9_-]{16}$/);
    expect(a).not.toBe(b);
  });

  it("computes the PKCE S256 challenge (RFC 7636 appendix B)", async () => {
    expect(await pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it.each([
    ["/jobs?x=1", "/jobs?x=1"],
    ["/profile/versions/2", "/profile/versions/2"],
    ["/a/../b", "/b"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["https://evil.example/", "/"],
    ["/\t/evil.example", "/"],
    ["javascript:alert(1)", "/"],
    ["/api/auth/sign-out", "/"],
    ["/.//evil.example", "/"],
    ["/%2e%2e//evil.example", "/"],
    ["", "/"],
    [null, "/"],
    ["/" + "a".repeat(600), "/"],
  ])("return_to %j -> %j", (raw, expected) => {
    expect(safeReturnTo(raw, APP_ORIGIN)).toBe(expected);
  });
});

// --- OIDC ------------------------------------------------------------------------------------

describe("OIDC against the tenant", () => {
  it("discovers the tenant, pinning the configured issuer", async () => {
    await fakeTenant();
    const discovery = await discover(entraConfig().entra);
    expect(discovery).toEqual({
      issuer: ISSUER,
      authorization_endpoint: ENDPOINTS.authorize,
      token_endpoint: ENDPOINTS.token,
      jwks_uri: ENDPOINTS.jwks,
      end_session_endpoint: ENDPOINTS.logout,
    });
  });

  it("refuses a discovery document for another issuer, or with an insecure endpoint", async () => {
    const tenant = await fakeTenant();
    tenant.setDiscovery({ issuer: "https://login.evil.example/v2.0" });
    await expect(discover(entraConfig().entra)).rejects.toMatchObject({ reason: "discovery_issuer_mismatch" });
    resetOidcCaches();
    tenant.setDiscovery({ issuer: ISSUER, token_endpoint: "http://easyapply-test.ciamlogin.com/token" });
    await expect(discover(entraConfig().entra)).rejects.toMatchObject({ reason: "discovery_invalid" });
  });

  it("asks for a code with PKCE S256, state, nonce and the account picker", async () => {
    await fakeTenant();
    const config = entraConfig();
    const url = new URL(authorizationUrl(await discover(config.entra), config.entra, { state: "STATE", nonce: "NONCE", challenge: "CHALLENGE" }));
    expect(`${url.origin}${url.pathname}`).toBe(ENDPOINTS.authorize);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: CLIENT_ID,
      response_type: "code",
      response_mode: "query",
      redirect_uri: `${APP_ORIGIN}/api/auth/callback`,
      scope: "openid profile email",
      state: "STATE",
      nonce: "NONCE",
      code_challenge: "CHALLENGE",
      code_challenge_method: "S256",
      prompt: "select_account",
    });
    expect(url.search).not.toContain("client-secret");
  });

  it("logs out at the tenant and returns to /signed-out", async () => {
    await fakeTenant();
    const config = entraConfig();
    const url = new URL(logoutUrl(await discover(config.entra), config.entra)!);
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(`${APP_ORIGIN}/signed-out`);
    expect(url.searchParams.has("id_token_hint")).toBe(false);
  });

  it("redeems a code with the secret and the verifier, and keeps only the ID token", async () => {
    const tenant = await fakeTenant();
    const config = entraConfig();
    tenant.codes.set("CODE", { idToken: "ID-TOKEN" });
    expect(await exchangeCode(await discover(config.entra), config.entra, { code: "CODE", verifier: "VERIFIER" })).toBe("ID-TOKEN");
    expect(Object.fromEntries(tenant.tokenRequests[0]!.form)).toEqual({
      grant_type: "authorization_code",
      code: "CODE",
      redirect_uri: `${APP_ORIGIN}/api/auth/callback`,
      client_id: CLIENT_ID,
      client_secret: "client-secret-for-tests-only",
      code_verifier: "VERIFIER",
    });
  });

  it("reports the provider's error code and AADSTS number, and nothing else", async () => {
    await fakeTenant();
    const config = entraConfig();
    const error = await exchangeCode(await discover(config.entra), config.entra, { code: "USED", verifier: "V" }).catch((e) => e);
    expect(error).toBeInstanceOf(SignInError);
    expect(error).toMatchObject({ reason: "token_endpoint_refused", providerError: { error: "invalid_grant", code: 54005 } });
  });

  describe("the ID token", () => {
    async function validate(claims: Record<string, unknown> = {}, options: { kid?: string; iatOffset?: number } = {}, nonce = "NONCE") {
      // A fresh tenant (and key) per call, so a key set cached from the last one must go.
      resetOidcCaches();
      const tenant = await fakeTenant();
      const config = entraConfig();
      return validateIdToken(await tenant.idToken(claims, options), await discover(config.entra), config.entra, nonce);
    }

    it("is accepted when everything holds, keeping only the claims EasyApply needs", async () => {
      expect(await validate({ roles: ["EasyApply.Operator"] })).toEqual({
        issuer: ISSUER,
        tenantId: TENANT,
        objectId: OID_A,
        email: "ada@example.test",
        name: "Ada Lovelace",
        roles: ["EasyApply.Operator"],
      });
    });

    it.each([
      ["another issuer", { iss: "https://login.evil.example/v2.0" }, "id_token_claim_iss"],
      ["another audience", { aud: "99999999-bbbb-4ccc-8ddd-eeeeeeeeeeee" }, "id_token_claim_aud"],
      ["two audiences", { aud: [CLIENT_ID, "99999999-bbbb-4ccc-8ddd-eeeeeeeeeeee"] }, "id_token_claim_aud"],
      ["another authorized party", { azp: "99999999-bbbb-4ccc-8ddd-eeeeeeeeeeee" }, "id_token_claim_azp"],
      ["another tenant", { tid: "99999999-2222-4333-8444-555555555555" }, "id_token_claim_tid"],
      ["no object id", { oid: undefined }, "id_token_claim_oid"],
      ["an object id that is not a GUID", { oid: "ada" }, "id_token_claim_oid"],
      ["no nonce", { nonce: undefined }, "id_token_claim_nonce"],
      ["no tenant", { tid: undefined }, "id_token_claim_tid"],
      ["roles that are not a list", { roles: "EasyApply.Operator" }, "id_token_claim_roles"],
    ])("is refused with %s", async (_what, claims, reason) => {
      await expect(validate(claims)).rejects.toMatchObject({ reason });
    });

    it("is refused for another sign-in's nonce", async () => {
      await expect(validate({}, {}, "ANOTHER-NONCE")).rejects.toMatchObject({ reason: "id_token_claim_nonce" });
    });

    it("says Microsoft is unavailable when its key set cannot be fetched", async () => {
      for (const answer of [() => HttpResponse.text("down", { status: 503 }), () => HttpResponse.error()]) {
        resetOidcCaches();
        const tenant = await fakeTenant();
        server.use(http.get(ENDPOINTS.jwks, answer));
        const config = entraConfig();
        const token = await tenant.idToken();
        await expect(validateIdToken(token, await discover(config.entra), config.entra, "NONCE")).rejects.toMatchObject({
          reason: "jwks_unreachable",
        });
      }
    });

    it("is refused when issued long ago, or signed by a key the tenant does not publish", async () => {
      await expect(validate({}, { iatOffset: -3600 })).rejects.toMatchObject({ reason: expect.stringMatching(/^id_token_/) });
      await expect(validate({}, { kid: "key-unknown" })).rejects.toMatchObject({ reason: "id_token_key" });
    });

    it("is refused when not RS256 — alg substitution cannot pass", async () => {
      const tenant = await fakeTenant();
      const config = entraConfig();
      const { SignJWT } = await import("jose");
      const forged = await new SignJWT({ oid: OID_A, tid: TENANT, nonce: "NONCE", sub: "x" })
        .setProtectedHeader({ alg: "HS256", kid: "key-1" })
        .setIssuer(ISSUER)
        .setAudience(CLIENT_ID)
        .setIssuedAt()
        .setExpirationTime("1h")
        .sign(new TextEncoder().encode(JSON.stringify(tenant.keySet.keys[0])));
      await expect(validateIdToken(forged, await discover(config.entra), config.entra, "NONCE")).rejects.toBeInstanceOf(SignInError);
    });

    it("keeps a valid name as it is — joiners, non-breaking spaces, emoji — and never fails on one", async () => {
      const name = "Ali" + "‌" + "reza" + " " + "\u{1F469}‍\u{1F4BB}";
      expect((await validate({ name })).name).toBe(name);
      expect((await validate({ name: "Bob\u0000\u0007" })).name).toBe("Bob");
      expect((await validate({ name: "N".repeat(300) })).name).toBe("N".repeat(255));
      expect((await validate({ name: "unknown" })).name).toBeNull();
      expect((await validate({ name: 42, email: undefined })).name).toBeNull();
      expect((await validate({ email: undefined })).email).toBeNull();
    });
  });

  it("replaces a lone surrogate, which no database column could store", () => {
    expect(displayText("Ada\ud800", 10)).toBe("Ada\ufffd");
    expect(displayText("\udc00Ada", 10)).toBe("\ufffdAda");
    expect(displayText("\u{1F469}", 10)).toBe("\u{1F469}"); // a real pair is kept
  });

  it("cleans display text the way the API does, counting code points", () => {
    expect(displayText("\u{1F469}".repeat(3), 2)).toBe("\u{1F469}\u{1F469}");
    expect(displayText("  \u0085\u0000 ", 10)).toBeNull();
    expect(displayText("é", 10)).toBe("é");
  });
});

describe("the session issued at the callback", () => {
  it("records the operator role as a flag, and nothing of Microsoft's tokens", async () => {
    const config = entraConfig();
    const token = await issueEntraSession(
      { issuer: ISSUER, tenantId: TENANT, objectId: OID_A, email: null, name: "Ada", roles: ["EasyApply.Operator", "Other"] },
      config,
    );
    const session = await verifyEntraSession(token, [SESSION_SECRET]);
    expect(session).toMatchObject({ op: true, oid: OID_A, email: null, name: "Ada" });
    expect(session!.exp - session!.iat).toBe(config.maxAgeSeconds);
    expect(Object.keys(session!).sort()).toEqual(["aud", "email", "exp", "iat", "iss", "name", "oid", "op", "sid", "tid", "typ"]);
  });

  it("mints an assertion carrying no role for a non-operator", async () => {
    const assertion = await mintAssertion(claims(), { operatorRole: "EasyApply.Operator" }, ASSERTION_SECRET);
    const { payload } = await jwtVerify(assertion, new TextEncoder().encode(ASSERTION_SECRET));
    expect(payload).not.toHaveProperty("roles");
    expect(payload.sid).toMatch(/^[A-Za-z0-9_-]{22}$/);
  });
});
