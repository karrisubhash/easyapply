// @vitest-environment node

/**
 * The identity seam's parts: the session token, the configuration it runs
 * under, what counts as an address, and `resolveIdentity` itself.
 */

import { readAuthConfig } from "./config";
import { isUsableEmail, normalizeEmail } from "./email";
import {
  accountTag,
  clearedSessionCookie,
  isHttps,
  issueSession,
  resolveIdentity,
  SESSION_COOKIE,
  sessionCookie,
  type CookieReader,
} from "./identity";
import { nowSeconds, signSession, verifySession } from "./session-token";

const SECRET = "a-development-session-secret-of-32+chars";
const OTHER_SECRET = "another-development-session-secret-32+";
const NOW = 1_800_000_000;

function jar(values: Record<string, string> = {}): CookieReader {
  return { get: (name) => values[name] };
}

function flip(text: string, index: number): string {
  const char = text[index] === "A" ? "B" : "A";
  return text.slice(0, index) + char + text.slice(index + 1);
}

describe("the session token", () => {
  const claims = { sub: "owner@easyapply.test", iat: NOW - 10, exp: NOW + 3600 };

  it("round-trips the claims it was signed with", async () => {
    const token = await signSession(claims, SECRET);
    expect(token.startsWith("v1.")).toBe(true);
    expect(await verifySession(token, SECRET, NOW)).toEqual(claims);
  });

  it("is refused when signed with another secret", async () => {
    expect(await verifySession(await signSession(claims, OTHER_SECRET), SECRET, NOW)).toBeNull();
  });

  it("is refused when its payload is edited to name someone else", async () => {
    const token = await signSession(claims, SECRET);
    const [version, , signature] = token.split(".");
    const forged = Buffer.from(JSON.stringify({ ...claims, sub: "victim@easyapply.test" })).toString("base64url");
    expect(await verifySession(`${version}.${forged}.${signature}`, SECRET, NOW)).toBeNull();
  });

  it("is refused when any character of the signature changes", async () => {
    const token = await signSession(claims, SECRET);
    expect(await verifySession(flip(token, token.length - 5), SECRET, NOW)).toBeNull();
  });

  it("is refused once expired, and when issued in the future", async () => {
    const token = await signSession(claims, SECRET);
    expect(await verifySession(token, SECRET, NOW + 3600)).toBeNull();
    const future = await signSession({ ...claims, iat: NOW + 600, exp: NOW + 7200 }, SECRET);
    expect(await verifySession(future, SECRET, NOW)).toBeNull();
  });

  it.each([
    ["an unusable address", { sub: "not an address", iat: NOW, exp: NOW + 60 }],
    ["an extra claim", { sub: "a@b.test", iat: NOW, exp: NOW + 60, admin: true }],
    ["a non-integer expiry", { sub: "a@b.test", iat: NOW, exp: "later" }],
    ["an expiry before issue", { sub: "a@b.test", iat: NOW, exp: NOW - 1 }],
  ])("is refused when signed but carrying %s", async (_what, bad) => {
    expect(await verifySession(await signSession(bad as never, SECRET), SECRET, NOW)).toBeNull();
  });

  it.each(["", "v1", "v1.a.b.c", "v2.eyJ9.sig", "v1.!!!.sig", "v1.e30.@@@", `v1.${"a".repeat(3000)}.sig`])(
    "never throws on garbage (%j)",
    async (garbage) => {
      expect(await verifySession(garbage, SECRET, NOW)).toBeNull();
    },
  );

  it("treats a missing cookie as no session", async () => {
    expect(await verifySession(undefined, SECRET, NOW)).toBeNull();
  });
});

describe("AUTH_MODE", () => {
  it("defaults to the development identity, as the proxy always has", () => {
    expect(readAuthConfig({})).toEqual({ mode: "development", devUserEmail: "dev@easyapply.local" });
    expect(readAuthConfig({ DEV_USER_EMAIL: "me@easyapply.test" })).toEqual({
      mode: "development",
      devUserEmail: "me@easyapply.test",
    });
  });

  it("forgives case and whitespace, nothing else", () => {
    expect(readAuthConfig({ AUTH_MODE: " Development " }).mode).toBe("development");
    expect(readAuthConfig({ AUTH_MODE: "DEV-SESSION", SESSION_SECRET: SECRET }).mode).toBe("dev-session");
  });

  it("fails closed on an unknown mode — never back to the development identity", () => {
    const config = readAuthConfig({ AUTH_MODE: "entra", DEV_USER_EMAIL: "me@easyapply.test" });
    expect(config.mode).toBe("misconfigured");
  });

  it("refuses dev-session without a secret long enough to sign with", () => {
    expect(readAuthConfig({ AUTH_MODE: "dev-session" }).mode).toBe("misconfigured");
    expect(readAuthConfig({ AUTH_MODE: "dev-session", SESSION_SECRET: "short" }).mode).toBe("misconfigured");
  });

  it("never puts the secret in the reason", () => {
    const config = readAuthConfig({ AUTH_MODE: "dev-session", SESSION_SECRET: "tooshort-but-secret" });
    expect(config.mode === "misconfigured" && config.reason).not.toContain("tooshort-but-secret");
  });

  it("takes a session lifetime within bounds, else refuses", () => {
    const base = { AUTH_MODE: "dev-session", SESSION_SECRET: SECRET };
    expect(readAuthConfig(base)).toMatchObject({ maxAgeSeconds: 43_200 });
    expect(readAuthConfig({ ...base, SESSION_MAX_AGE_SECONDS: "3600" })).toMatchObject({ maxAgeSeconds: 3600 });
    for (const bad of ["0", "59", "abc", "1.5", "99999999"]) {
      expect(readAuthConfig({ ...base, SESSION_MAX_AGE_SECONDS: bad }).mode).toBe("misconfigured");
    }
  });
});

describe("what the sign-in accepts as an address", () => {
  it("normalises the way the API's seam does", () => {
    expect(normalizeEmail("  Owner@EasyApply.TEST ")).toBe("owner@easyapply.test");
  });

  it.each([
    ["owner@easyapply.test", true],
    ["a@b", true],
    ["", false],
    ["no-at-sign", false],
    ["@easyapply.test", false],
    ["owner@", false],
    ["two@@easyapply.test", false],
    ["with space@easyapply.test", false],
    ["tab\t@easyapply.test", false],
    ["nul\u0000@easyapply.test", false],
    [`${"a".repeat(250)}@b.test`, false],
  ])("%j -> %s", (value, expected) => {
    expect(isUsableEmail(value)).toBe(expected);
  });
});

describe("resolveIdentity — the seam", () => {
  const devSession = readAuthConfig({ AUTH_MODE: "dev-session", SESSION_SECRET: SECRET });

  it("development: the configured person, whatever the request carries", async () => {
    const config = readAuthConfig({ DEV_USER_EMAIL: "configured@easyapply.test" });
    const token = await signSession({ sub: "someone@easyapply.test", iat: nowSeconds(), exp: nowSeconds() + 60 }, SECRET);
    const identity = await resolveIdentity(jar({ [SESSION_COOKIE]: token }), config);
    expect(identity).toEqual({
      status: "authenticated",
      mode: "development",
      email: "configured@easyapply.test",
      name: null,
      label: "configured@easyapply.test",
      sessionTag: await accountTag("development", "configured@easyapply.test"),
      operator: true,
      upstreamHeaders: expect.any(Function),
    });
    if (identity.status !== "authenticated") throw new Error("identity");
    expect(await identity.upstreamHeaders()).toEqual({ "X-Dev-User-Email": "configured@easyapply.test" });
  });

  it("dev-session: the person the session was issued for", async () => {
    if (devSession.mode !== "dev-session") throw new Error("config");
    const token = await issueSession("owner@easyapply.test", devSession);
    const identity = await resolveIdentity(jar({ [SESSION_COOKIE]: token }), devSession);
    expect(identity).toEqual({
      status: "authenticated",
      mode: "dev-session",
      email: "owner@easyapply.test",
      name: null,
      label: "owner@easyapply.test",
      sessionTag: await accountTag("dev-session", "owner@easyapply.test"),
      operator: true,
      upstreamHeaders: expect.any(Function),
    });
    if (identity.status !== "authenticated") throw new Error("identity");
    expect(await identity.upstreamHeaders()).toEqual({ "X-Dev-User-Email": "owner@easyapply.test" });
  });

  it("the account tag is the account's: the same person signing in again keeps it, another changes it", async () => {
    if (devSession.mode !== "dev-session") throw new Error("config");
    const first = await resolveIdentity(jar({ [SESSION_COOKIE]: await issueSession("owner@easyapply.test", devSession) }), devSession);
    const again = await resolveIdentity(jar({ [SESSION_COOKIE]: await issueSession("owner@easyapply.test", devSession) }), devSession);
    const other = await resolveIdentity(jar({ [SESSION_COOKIE]: await issueSession("other@easyapply.test", devSession) }), devSession);
    if (first.status !== "authenticated" || again.status !== "authenticated" || other.status !== "authenticated") {
      throw new Error("identity");
    }
    expect(first.sessionTag).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(again.sessionTag).toBe(first.sessionTag);
    expect(other.sessionTag).not.toBe(first.sessionTag);
    // Mode is part of it: the same address in another mode is another account.
    expect(await accountTag("development", "owner@easyapply.test")).not.toBe(first.sessionTag);
  });

  it("dev-session: no cookie, a forged one, or one from another server is signed out", async () => {
    const other = await signSession({ sub: "a@b.test", iat: nowSeconds(), exp: nowSeconds() + 60 }, OTHER_SECRET);
    for (const cookies of [jar(), jar({ [SESSION_COOKIE]: "v1.e30.AAAA" }), jar({ [SESSION_COOKIE]: other })]) {
      expect(await resolveIdentity(cookies, devSession)).toEqual({ status: "signed_out", mode: "dev-session" });
    }
  });

  it("misconfigured: says so, and names no identity", async () => {
    const identity = await resolveIdentity(jar(), readAuthConfig({ AUTH_MODE: "nope" }));
    expect(identity.status).toBe("misconfigured");
    expect(identity).not.toHaveProperty("email");
    expect(identity).not.toHaveProperty("upstreamHeaders");
  });

  it("reads the process environment when not handed a config", async () => {
    vi.stubEnv("AUTH_MODE", "");
    vi.stubEnv("DEV_USER_EMAIL", "from-env@easyapply.test");
    try {
      expect(await resolveIdentity(jar())).toMatchObject({ email: "from-env@easyapply.test" });
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("the session cookie", () => {
  it("is HttpOnly, SameSite=Lax, host-only for the whole app, and expires", () => {
    const cookie = sessionCookie("TOKEN", 3600, false);
    expect(cookie).toBe(`${SESSION_COOKIE}=TOKEN; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax`);
    expect(cookie).not.toMatch(/Domain=/i);
  });

  it("is Secure whenever the app is reached over https", () => {
    expect(sessionCookie("TOKEN", 60, true)).toMatch(/; Secure$/);
    expect(clearedSessionCookie(true)).toMatch(/Max-Age=0; HttpOnly; SameSite=Lax; Secure$/);
  });

  it("knows https directly or from a proxy's X-Forwarded-Proto", () => {
    expect(isHttps(new Request("https://app.example/x"))).toBe(true);
    expect(isHttps(new Request("http://localhost:3000/x"))).toBe(false);
    expect(isHttps(new Request("http://app/x", { headers: { "x-forwarded-proto": "https, http" } }))).toBe(true);
  });
});
