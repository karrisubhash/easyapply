// @vitest-environment node

/**
 * The session routes: GET /api/auth/session (read by the web UI and the
 * extension), and the development sign-in and sign-out.
 *
 * Called directly with real NextRequests. What is asserted is the proxy's
 * security model applied to them: the Host check on everything, same-origin
 * only for the writes — the extension included, since signing in is not
 * something it does — and never the token in a body.
 */

import { NextRequest } from "next/server";

import { accountTag, SESSION_COOKIE } from "@/server/auth/identity";

import { GET as session } from "./session/route";
import { POST as signIn } from "./sign-in/route";
import { POST as signOut } from "./sign-out/route";

const SECRET = "a-development-session-secret-of-32+chars";
const EXTENSION_ID = "jjhciekcmhmngciccfgignmmlgjknakk";
const SAME_ORIGIN = { host: "localhost:3000", origin: "http://localhost:3000", "sec-fetch-site": "same-origin" };

function request(path: string, init: { method?: string; headers?: Record<string, string>; body?: string } = {}) {
  return new NextRequest(`http://localhost:3000${path}`, init);
}

function devSession() {
  vi.stubEnv("AUTH_MODE", "dev-session");
  vi.stubEnv("SESSION_SECRET", SECRET);
}

/** The session cookie a Set-Cookie header carries, as a browser would send it back. */
function cookieFrom(response: Response): string {
  const header = response.headers.get("set-cookie") ?? "";
  const pair = header.split(";")[0] ?? "";
  expect(pair.startsWith(`${SESSION_COOKIE}=`)).toBe(true);
  return pair;
}

async function signInAs(email: string): Promise<Response> {
  return signIn(
    request("/api/auth/sign-in", {
      method: "POST",
      headers: { ...SAME_ORIGIN, "content-type": "application/json" },
      body: JSON.stringify({ email }),
    }),
  );
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /api/auth/session", () => {
  it("development: the configured identity, and no cookie is needed", async () => {
    vi.stubEnv("DEV_USER_EMAIL", "configured@easyapply.test");
    const response = await session(request("/api/auth/session", { headers: { host: "localhost:3000" } }));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({
      mode: "development",
      authenticated: true,
      email: "configured@easyapply.test",
      name: null,
      sessionTag: await accountTag("development", "configured@easyapply.test"),
    });
  });

  it("dev-session: signed out without a cookie", async () => {
    devSession();
    const response = await session(request("/api/auth/session", { headers: { host: "localhost:3000" } }));
    expect(await response.json()).toEqual({ mode: "dev-session", authenticated: false, email: null, name: null, sessionTag: null });
  });

  it("dev-session: the signed-in person — and the answer never carries the token", async () => {
    devSession();
    const cookie = cookieFrom(await signInAs("owner@easyapply.test"));
    const response = await session(
      request("/api/auth/session", { headers: { host: "localhost:3000", cookie } }),
    );
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({
      mode: "dev-session",
      authenticated: true,
      email: "owner@easyapply.test",
      name: null,
      sessionTag: await accountTag("dev-session", "owner@easyapply.test"),
    });
    expect(text).not.toContain(cookie.split("=")[1]);
  });

  it("answers the extension's read, which carries the cookie like any other", async () => {
    devSession();
    const cookie = cookieFrom(await signInAs("owner@easyapply.test"));
    const response = await session(
      request("/api/auth/session", {
        headers: { host: "localhost:3000", origin: `chrome-extension://${EXTENSION_ID}`, "sec-fetch-site": "none", cookie },
      }),
    );
    expect(await response.json()).toMatchObject({ authenticated: true, email: "owner@easyapply.test" });
  });

  it("refuses a rebound host — the one way a page could read the answer", async () => {
    const response = await session(request("/api/auth/session", { headers: { host: "evil.example:3000" } }));
    expect(response.status).toBe(403);
    expect((await response.json()).code).toBe("host_not_allowed");
  });

  it("says a misconfigured server is misconfigured, and nothing else", async () => {
    vi.stubEnv("AUTH_MODE", "dev-session");
    vi.stubEnv("SESSION_SECRET", "short");
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await session(request("/api/auth/session", { headers: { host: "localhost:3000" } }));
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ detail: "Sign-in is misconfigured on this server.", code: "auth_misconfigured" });
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });
});

describe("POST /api/auth/sign-in", () => {
  it("issues an HttpOnly, SameSite=Lax session for the normalised address", async () => {
    devSession();
    const response = await signInAs("  Owner@EasyApply.TEST ");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      mode: "dev-session",
      authenticated: true,
      email: "owner@easyapply.test",
      name: null,
      sessionTag: await accountTag("dev-session", "owner@easyapply.test"),
    });
    const header = response.headers.get("set-cookie") ?? "";
    expect(header).toMatch(new RegExp(`^${SESSION_COOKIE}=v1\\.`));
    expect(header).toContain("HttpOnly");
    expect(header).toContain("SameSite=Lax");
    expect(header).toContain("Path=/");
    expect(header).toContain("Max-Age=43200");
    expect(header).not.toContain("Secure");
  });

  it("marks the cookie Secure over https", async () => {
    devSession();
    const response = await signIn(
      new NextRequest("https://app.example/api/auth/sign-in", {
        method: "POST",
        headers: { host: "localhost", origin: "https://localhost", "sec-fetch-site": "same-origin", "content-type": "application/json" },
        body: JSON.stringify({ email: "owner@easyapply.test" }),
      }),
    );
    expect(response.headers.get("set-cookie")).toContain("Secure");
  });

  it.each([
    ["a cross-site page (login CSRF)", { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" }],
    ["the extension, even when it may save jobs", { host: "localhost:3000", origin: `chrome-extension://${EXTENSION_ID}`, "sec-fetch-site": "none" }],
    ["a request with neither Origin nor Sec-Fetch-Site", { host: "localhost:3000" }],
  ])("refuses %s, and sets no cookie", async (_who, headers) => {
    devSession();
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await signIn(
      request("/api/auth/sign-in", {
        method: "POST",
        headers: { ...headers, "content-type": "application/json" },
        body: JSON.stringify({ email: "attacker@evil.example" }),
      }),
    );
    expect(response.status).toBe(403);
    expect((await response.json()).code).toBe("cross_site_request");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("refuses a rebound host before anything else", async () => {
    devSession();
    const response = await signIn(
      request("/api/auth/sign-in", {
        method: "POST",
        headers: { host: "evil.example:3000", origin: "http://evil.example:3000", "sec-fetch-site": "same-origin" },
        body: JSON.stringify({ email: "a@b.test" }),
      }),
    );
    expect((await response.json()).code).toBe("host_not_allowed");
  });

  it.each([
    ["no body", undefined],
    ["not JSON", "email=a@b.test"],
    ["no email field", JSON.stringify({ user: "a@b.test" })],
    ["a non-string email", JSON.stringify({ email: ["a@b.test"] })],
    ["not an address", JSON.stringify({ email: "nobody" })],
    ["an oversized body", JSON.stringify({ email: "a@b.test", pad: "x".repeat(2000) })],
  ])("refuses %s with 422", async (_what, body) => {
    devSession();
    const response = await signIn(
      request("/api/auth/sign-in", { method: "POST", headers: { ...SAME_ORIGIN, "content-type": "application/json" }, body }),
    );
    expect(response.status).toBe(422);
    expect((await response.json()).code).toBe("invalid_email");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("is not available under the development identity", async () => {
    const response = await signInAs("owner@easyapply.test");
    expect(response.status).toBe(404);
    expect((await response.json()).code).toBe("sign_in_not_available");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("refuses to sign anyone in on a misconfigured server", async () => {
    vi.stubEnv("AUTH_MODE", "dev-session");
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await signInAs("owner@easyapply.test");
    expect(response.status).toBe(500);
    expect(response.headers.get("set-cookie")).toBeNull();
    error.mockRestore();
  });
});

describe("POST /api/auth/sign-out", () => {
  it("clears the session, and the next read is signed out", async () => {
    devSession();
    const response = await signOut(request("/api/auth/sign-out", { method: "POST", headers: SAME_ORIGIN }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ mode: "dev-session", authenticated: false, email: null, name: null, sessionTag: null });
    expect(response.headers.get("set-cookie")).toBe(`${SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`);
  });

  it.each([
    ["a cross-site page", { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" }],
    ["the extension", { host: "localhost:3000", origin: `chrome-extension://${EXTENSION_ID}`, "sec-fetch-site": "none" }],
  ])("refuses %s", async (_who, headers) => {
    devSession();
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await signOut(request("/api/auth/sign-out", { method: "POST", headers }));
    expect(response.status).toBe(403);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("is not available under the development identity", async () => {
    const response = await signOut(request("/api/auth/sign-out", { method: "POST", headers: SAME_ORIGIN }));
    expect(response.status).toBe(404);
  });
});
