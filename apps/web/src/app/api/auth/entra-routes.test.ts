// @vitest-environment node

/**
 * Real sign-in through the routes: POST /api/auth/sign-in → Microsoft (a fake
 * tenant at the network boundary, real RS256 keys) → GET /api/auth/callback →
 * the EasyApply session, then GET /api/auth/session and POST /api/auth/sign-out.
 */

import { http, HttpResponse } from "msw";
import { NextRequest } from "next/server";

import { accountTag, SESSION_COOKIE } from "@/server/auth/identity";
import { resetOidcCaches } from "@/server/auth/oidc";
import {
  ACCESS_TOKEN,
  APP_ORIGIN,
  AUTHORITY,
  ENDPOINTS,
  fakeTenant,
  OID_A,
  OID_B,
  REFRESH_TOKEN,
  stubEntraEnv,
  TENANT,
  type FakeTenant,
} from "@/test/entra";
import { server } from "@/test/server";

import { GET as callback } from "./callback/route";
import { GET as session } from "./session/route";
import { POST as signIn } from "./sign-in/route";
import { POST as signOut } from "./sign-out/route";

const SAME_ORIGIN = { host: "localhost:3000", origin: APP_ORIGIN, "sec-fetch-site": "same-origin" };
const FORM = { "content-type": "application/x-www-form-urlencoded" };

let tenant: FakeTenant;
let logs: string[];

beforeEach(async () => {
  resetOidcCaches();
  stubEntraEnv();
  tenant = await fakeTenant();
  logs = [];
  for (const level of ["info", "warn", "error"] as const) {
    vi.spyOn(console, level).mockImplementation((...args: unknown[]) => {
      logs.push(args.map(String).join(" "));
    });
  }
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function cookiePair(setCookie: string): string {
  return setCookie.split(";")[0] ?? "";
}

async function startSignIn(returnTo = "/jobs", headers: Record<string, string> = SAME_ORIGIN) {
  const response = await signIn(
    new NextRequest(`${APP_ORIGIN}/api/auth/sign-in`, {
      method: "POST",
      headers: { ...headers, ...FORM },
      body: new URLSearchParams({ return_to: returnTo }).toString(),
    }),
  );
  const location = response.headers.get("location") ?? "";
  const url = location.startsWith("http") ? new URL(location) : null;
  const txCookie = response.headers.getSetCookie().find((c) => c.startsWith("easyapply_oidc_")) ?? "";
  return { response, url, state: url?.searchParams.get("state") ?? "", nonce: url?.searchParams.get("nonce") ?? "", txCookie };
}

function returnFromMicrosoft(query: Record<string, string>, cookies: string[]) {
  return callback(
    new NextRequest(`${APP_ORIGIN}/api/auth/callback?${new URLSearchParams(query)}`, {
      headers: { host: "localhost:3000", cookie: cookies.filter(Boolean).join("; ") },
    }),
  );
}

async function signInAs(oid = OID_A, claims: Record<string, unknown> = {}) {
  const started = await startSignIn();
  const idToken = await tenant.idToken({ oid, nonce: started.nonce, ...claims });
  tenant.codes.set(`code-${oid}`, { idToken });
  const done = await returnFromMicrosoft({ code: `code-${oid}`, state: started.state }, [cookiePair(started.txCookie)]);
  const sessionCookie = done.headers.getSetCookie().find((c) => c.startsWith(`${SESSION_COOKIE}=v2.`)) ?? "";
  return { started, done, sessionCookie, idToken };
}

function readSession(cookie: string) {
  return session(new NextRequest(`${APP_ORIGIN}/api/auth/session`, { headers: { host: "localhost:3000", cookie } }));
}

describe("POST /api/auth/sign-in (entra)", () => {
  it("sends the browser to Microsoft with PKCE, keeping the transaction in its own sealed cookie", async () => {
    const { response, url, state, txCookie } = await startSignIn();
    expect(response.status).toBe(303);
    expect(`${url!.origin}${url!.pathname}`).toBe(ENDPOINTS.authorize);
    expect(url!.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url!.searchParams.get("prompt")).toBe("select_account");
    expect(state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(txCookie).toMatch(/^easyapply_oidc_[A-Za-z0-9_-]{16}=/);
    expect(txCookie).toContain("Path=/api/auth/callback");
    expect(txCookie).toContain("Max-Age=600");
    expect(txCookie).toContain("HttpOnly");
    expect(txCookie).toContain("SameSite=Lax");
    // Sealed: neither the verifier nor the nonce is readable in it.
    expect(txCookie).not.toContain(url!.searchParams.get("nonce")!);
    // No session is issued by starting to sign in.
    expect(response.headers.getSetCookie().some((c) => c.startsWith(`${SESSION_COOKIE}=`))).toBe(false);
  });

  it("refuses a cross-site page (login CSRF) and the extension", async () => {
    for (const headers of [
      { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
      { host: "localhost:3000", origin: "chrome-extension://jjhciekcmhmngciccfgignmmlgjknakk", "sec-fetch-site": "none" },
    ]) {
      const { response } = await startSignIn("/", headers);
      expect(response.status).toBe(403);
      expect(response.headers.getSetCookie()).toEqual([]);
    }
  });

  it("starts over on APP_ORIGIN when reached by another name for this server", async () => {
    const { response, txCookie } = await startSignIn("/", { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", "sec-fetch-site": "same-origin" });
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/`);
    expect(txCookie).toBe("");
  });

  it("says Microsoft is unavailable when discovery fails, and sets nothing", async () => {
    server.use(http.get(`${AUTHORITY}/.well-known/openid-configuration`, () => HttpResponse.text("down", { status: 503 })));
    const { response, txCookie } = await startSignIn();
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=unavailable`);
    expect(txCookie).toBe("");
  });
});

describe("GET /api/auth/callback", () => {
  it("issues the EasyApply session, clears the transaction, and returns where sign-in began", async () => {
    const { done, sessionCookie, started } = await signInAs(OID_A);
    expect(done.status).toBe(303);
    expect(done.headers.get("location")).toBe(`${APP_ORIGIN}/jobs`);
    expect(sessionCookie).toContain("HttpOnly");
    expect(sessionCookie).toContain("SameSite=Lax");
    expect(sessionCookie).toContain("Path=/");
    expect(sessionCookie).toContain(`Max-Age=${8 * 3600}`);
    const cleared = done.headers.getSetCookie().find((c) => c.startsWith(cookiePair(started.txCookie).split("=")[0]!));
    expect(cleared).toContain("Max-Age=0");
    expect(cleared).toContain("Path=/api/auth/callback");

    const body = await (await readSession(cookiePair(sessionCookie))).json();
    expect(body).toEqual({
      mode: "entra",
      authenticated: true,
      email: "ada@example.test",
      name: "Ada Lovelace",
      sessionTag: await accountTag("entra", `${TENANT}:${OID_A}`),
    });
  });

  it("keeps no Microsoft token anywhere it answers", async () => {
    const { done, sessionCookie, idToken } = await signInAs(OID_A);
    const payload = Buffer.from(cookiePair(sessionCookie).split(".")[1] ?? "", "base64url").toString();
    const everything = [...done.headers.values(), await done.text(), payload].join("\n");
    for (const secret of [idToken, idToken.split(".")[2]!, ACCESS_TOKEN, REFRESH_TOKEN, "client-secret-for-tests-only"]) {
      expect(everything).not.toContain(secret);
    }
    expect(Object.keys(JSON.parse(payload)).sort()).toEqual(["aud", "email", "exp", "iat", "iss", "name", "oid", "op", "sid", "tid", "typ"]);
    // The logs: categories, never a token, a code, an email or an object id.
    const logged = logs.join("\n");
    for (const secret of [idToken, "code-", "ada@example.test", OID_A, ACCESS_TOKEN]) expect(logged).not.toContain(secret);
  });

  it("a Secure cookie when the app is served over https", async () => {
    stubEntraEnv({ APP_ORIGIN: "https://app.easyapply.example", PROXY_ALLOWED_HOSTS: "app.easyapply.example" });
    const started = await signIn(
      new NextRequest("https://app.easyapply.example/api/auth/sign-in", {
        method: "POST",
        headers: { host: "app.easyapply.example", origin: "https://app.easyapply.example", "sec-fetch-site": "same-origin", ...FORM },
        body: "return_to=%2F",
      }),
    );
    const transaction = started.headers.getSetCookie()[0]!;
    expect(transaction).toMatch(/; Secure$/);
    // Prefixed, so nothing over http — and no sibling subdomain — can set one.
    expect(transaction).toMatch(/^__Host-easyapply_oidc_/);
    expect(transaction).toContain("Path=/;");
    expect(transaction).not.toMatch(/Domain=/i);

    // The whole round trip over https: the session cookie is __Host- prefixed.
    const url = new URL(started.headers.get("location")!);
    tenant.codes.set("https-code", { idToken: await tenant.idToken({ nonce: url.searchParams.get("nonce")! }) });
    const done = await callback(
      new NextRequest(`https://app.easyapply.example/api/auth/callback?code=https-code&state=${url.searchParams.get("state")}`, {
        headers: { host: "app.easyapply.example", cookie: cookiePair(transaction) },
      }),
    );
    const issued = done.headers.getSetCookie().find((c) => c.startsWith("__Host-easyapply_session=v2."));
    expect(issued).toBeDefined();
    expect(issued).toContain("Path=/");
    expect(issued).toMatch(/; Secure$/);
    expect(issued).not.toMatch(/Domain=/i);
    // Read back under that name only: the plain name is not this deployment's session.
    const read = (cookie: string) =>
      session(new NextRequest("https://app.easyapply.example/api/auth/session", { headers: { host: "app.easyapply.example", cookie } }));
    const value = cookiePair(issued!).split("=").slice(1).join("=");
    expect((await (await read(`__Host-easyapply_session=${value}`)).json()).authenticated).toBe(true);
    expect((await (await read(`easyapply_session=${value}`)).json()).authenticated).toBe(false);
  });

  it.each([
    ["no state at all", () => ({}), "failed"],
    ["a state no transaction in this browser has", () => ({ state: "x".repeat(43), code: "c" }), "expired"],
  ])("refuses %s", async (_what, query, code) => {
    const response = await returnFromMicrosoft(query() as Record<string, string>, []);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=${code}`);
    expect(response.headers.getSetCookie().some((c) => c.startsWith(`${SESSION_COOKIE}=`))).toBe(false);
  });

  it("refuses an attacker's code and state delivered to this browser (login CSRF)", async () => {
    // The attacker starts a sign-in in THEIR browser and sends ours the callback URL.
    const theirs = await startSignIn();
    tenant.codes.set("attacker-code", { idToken: await tenant.idToken({ oid: OID_B, nonce: theirs.nonce }) });
    const ours = await startSignIn();
    const response = await returnFromMicrosoft({ code: "attacker-code", state: theirs.state }, [cookiePair(ours.txCookie)]);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=expired`);
    expect(response.headers.getSetCookie().some((c) => c.startsWith(`${SESSION_COOKIE}=`))).toBe(false);
    expect(tenant.tokenRequests).toEqual([]); // never even redeemed
  });

  it("says a cancelled sign-in was cancelled, and clears its transaction", async () => {
    const started = await startSignIn();
    const response = await returnFromMicrosoft(
      { error: "access_denied", error_description: "AADSTS50126: the user cancelled", state: started.state },
      [cookiePair(started.txCookie)],
    );
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=cancelled`);
    expect(response.headers.getSetCookie().join()).toContain("Max-Age=0");
    expect(logs.join("\n")).toContain('"error":"access_denied"');
    expect(logs.join("\n")).toContain('"aadsts":50126');
    expect(logs.join("\n")).not.toContain("the user cancelled");
  });

  it("refuses an ID token minted for another sign-in (nonce), another tenant, or without an oid", async () => {
    for (const claims of [{ nonce: "someone-elses" }, { tid: "99999999-2222-4333-8444-555555555555" }, { oid: undefined }]) {
      const started = await startSignIn();
      tenant.codes.set("code", { idToken: await tenant.idToken({ nonce: started.nonce, ...claims }) });
      const response = await returnFromMicrosoft({ code: "code", state: started.state }, [cookiePair(started.txCookie)]);
      expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=failed`);
      expect(response.headers.getSetCookie().some((c) => c.startsWith(`${SESSION_COOKIE}=`))).toBe(false);
    }
  });

  it("a code already used reads as expired: sign in again", async () => {
    const started = await startSignIn();
    const response = await returnFromMicrosoft({ code: "never-issued", state: started.state }, [cookiePair(started.txCookie)]);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=expired`);
  });

  it("says Microsoft is unavailable when the token endpoint cannot be reached", async () => {
    const started = await startSignIn();
    server.use(http.post(ENDPOINTS.token, () => HttpResponse.error()));
    const response = await returnFromMicrosoft({ code: "code", state: started.state }, [cookiePair(started.txCookie)]);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/?auth_error=unavailable`);
  });

  it("a failed sign-in leaves the session the browser already has alone", async () => {
    const { sessionCookie } = await signInAs(OID_A);
    const started = await startSignIn();
    const response = await returnFromMicrosoft({ error: "access_denied", state: started.state }, [
      cookiePair(started.txCookie),
      cookiePair(sessionCookie),
    ]);
    expect(response.headers.getSetCookie().some((c) => c.startsWith(`${SESSION_COOKIE}=`))).toBe(false);
    expect((await (await readSession(cookiePair(sessionCookie))).json()).authenticated).toBe(true);
  });

  it("two sign-ins in two tabs each complete", async () => {
    const one = await startSignIn("/one");
    const two = await startSignIn("/two");
    tenant.codes.set("c1", { idToken: await tenant.idToken({ nonce: one.nonce }) });
    tenant.codes.set("c2", { idToken: await tenant.idToken({ nonce: two.nonce }) });
    const both = [cookiePair(one.txCookie), cookiePair(two.txCookie)];
    expect((await returnFromMicrosoft({ code: "c1", state: one.state }, both)).headers.get("location")).toBe(`${APP_ORIGIN}/one`);
    expect((await returnFromMicrosoft({ code: "c2", state: two.state }, both)).headers.get("location")).toBe(`${APP_ORIGIN}/two`);
  });

  it("an open redirect in return_to lands on the home page instead", async () => {
    const started = await startSignIn("//evil.example/steal");
    tenant.codes.set("c", { idToken: await tenant.idToken({ nonce: started.nonce }) });
    const response = await returnFromMicrosoft({ code: "c", state: started.state }, [cookiePair(started.txCookie)]);
    expect(response.headers.get("location")).toBe(`${APP_ORIGIN}/`);
  });

  it("the operator role becomes the session's operator flag", async () => {
    const { sessionCookie } = await signInAs(OID_A, { roles: ["EasyApply.Operator"] });
    const payload = JSON.parse(Buffer.from(cookiePair(sessionCookie).split(".")[1]!, "base64url").toString());
    expect(payload.op).toBe(true);
    expect(logs.join("\n")).toContain('auth.sign_in.success {"operator":true}');
  });

  it("does not exist outside real sign-in", async () => {
    vi.unstubAllEnvs();
    vi.stubEnv("AUTH_MODE", "development");
    const response = await returnFromMicrosoft({ code: "c", state: "x".repeat(43) }, []);
    expect(response.status).toBe(404);
  });
});

describe("the session and sign-out (entra)", () => {
  it("a signed-out browser is signed out, in the entra mode", async () => {
    expect(await (await readSession("")).json()).toEqual({ mode: "entra", authenticated: false, email: null, name: null, sessionTag: null });
  });

  it("signing out clears the cookie and hands back Microsoft's end-session URL", async () => {
    const { sessionCookie } = await signInAs(OID_A);
    const response = await signOut(
      new NextRequest(`${APP_ORIGIN}/api/auth/sign-out`, { method: "POST", headers: { ...SAME_ORIGIN, cookie: cookiePair(sessionCookie) } }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toBe(`${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
    const body = await response.json();
    expect(body).toMatchObject({ mode: "entra", authenticated: false, email: null });
    const logout = new URL(body.logoutUrl);
    expect(`${logout.origin}${logout.pathname}`).toBe(ENDPOINTS.logout);
    expect(logout.searchParams.get("post_logout_redirect_uri")).toBe(`${APP_ORIGIN}/signed-out`);
  });

  it("still signs out of EasyApply when Microsoft cannot be reached", async () => {
    resetOidcCaches();
    server.use(http.get(`${AUTHORITY}/.well-known/openid-configuration`, () => HttpResponse.error()));
    const response = await signOut(new NextRequest(`${APP_ORIGIN}/api/auth/sign-out`, { method: "POST", headers: SAME_ORIGIN }));
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    expect((await response.json()).logoutUrl).toBe(`${APP_ORIGIN}/signed-out`);
  });

  it("refuses a cross-site sign-out", async () => {
    const response = await signOut(
      new NextRequest(`${APP_ORIGIN}/api/auth/sign-out`, {
        method: "POST",
        headers: { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
      }),
    );
    expect(response.status).toBe(403);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("a forged or edited session cookie is simply signed out", async () => {
    const { sessionCookie } = await signInAs(OID_A);
    const [name, token] = cookiePair(sessionCookie).split("=") as [string, string];
    const [version, payload, signature] = token.split(".") as [string, string, string];
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    claims.oid = OID_B;
    const edited = `${name}=${version}.${Buffer.from(JSON.stringify(claims)).toString("base64url")}.${signature}`;
    expect((await (await readSession(edited)).json()).authenticated).toBe(false);
    expect((await (await readSession(`${SESSION_COOKIE}=v2.e30.AAAA`)).json()).authenticated).toBe(false);
  });
});
