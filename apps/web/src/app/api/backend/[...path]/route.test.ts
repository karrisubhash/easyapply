// @vitest-environment node

/**
 * The proxy's two refusals: a host it does not answer for, and a
 * state-changing request that cannot prove it came from this origin.
 *
 * The route handler is called directly with a real NextRequest, and the API
 * behind it is msw at the network boundary — so what is asserted is what the
 * proxy would have put on the wire, or that it put nothing there at all.
 */

import { http, HttpResponse } from "msw";
import { NextRequest } from "next/server";

import { server } from "@/test/server";

import { GET, POST, PUT } from "./route";

const UPSTREAM = "http://127.0.0.1:8000/api/v1";

type Forwarded = { method: string; path: string; headers: Headers; body: string };
let forwarded: Forwarded[] = [];

beforeEach(() => {
  forwarded = [];
  server.use(
    http.all(`${UPSTREAM}/*`, async ({ request }) => {
      forwarded.push({
        method: request.method,
        path: new URL(request.url).pathname,
        headers: request.headers,
        body: await request.text(),
      });
      return HttpResponse.json({ ok: true });
    }),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function call(
  handler: typeof GET,
  path: string[],
  { method = "GET", headers = {}, body }: { method?: string; headers?: Record<string, string>; body?: BodyInit },
) {
  const request = new NextRequest(`http://localhost:3000/api/backend/${path.join("/")}`, {
    method,
    headers,
    body,
  });
  return handler(request, { params: Promise.resolve({ path }) });
}

const SAME_ORIGIN = {
  host: "localhost:3000",
  origin: "http://localhost:3000",
  "sec-fetch-site": "same-origin",
};

async function expectRefused(response: Response, code: string) {
  expect(response.status).toBe(403);
  expect(response.headers.get("content-type")).toContain("application/json");
  const body = await response.json();
  expect(body.code).toBe(code);
  expect(typeof body.detail).toBe("string");
  expect(forwarded).toEqual([]);
}

describe("cross-site requests", () => {
  it("refuses a cross-site multipart upload without forwarding it", async () => {
    const form = new FormData();
    form.append("file", new Blob(["%PDF-1.7"], { type: "application/pdf" }), "resume.pdf");

    const response = await call(POST, ["resumes"], {
      method: "POST",
      headers: { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
      body: form,
    });

    await expectRefused(response, "cross_site_request");
  });

  it.each([
    ["profile", "versions"],
    ["resumes", "7", "reprocess"],
    ["jobs", "7", "reprocess"],
  ])("refuses a cross-site body-less POST to /%s", async (...path) => {
    const response = await call(POST, path, {
      method: "POST",
      headers: { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
    });
    await expectRefused(response, "cross_site_request");
  });

  it("refuses another port on the same machine: same-site is not same-origin", async () => {
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { host: "localhost:3000", origin: "http://localhost:3001", "sec-fetch-site": "same-site" },
    });
    await expectRefused(response, "cross_site_request");
  });

  it("refuses a mismatched Origin from a browser that sends no Sec-Fetch-Site", async () => {
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { host: "localhost:3000", origin: "https://evil.example" },
    });
    await expectRefused(response, "cross_site_request");
  });

  it("refuses an opaque origin", async () => {
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { host: "localhost:3000", origin: "null" },
    });
    await expectRefused(response, "cross_site_request");
  });

  it("refuses a state-changing request that carries neither header", async () => {
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { host: "localhost:3000" },
    });
    await expectRefused(response, "cross_site_request");
  });

  it("guards PUT exactly as it guards POST", async () => {
    const response = await call(PUT, ["profile", "versions", "2", "snapshot"], {
      method: "PUT",
      headers: { host: "localhost:3000", origin: "https://evil.example", "sec-fetch-site": "cross-site" },
      body: "{}",
    });
    await expectRefused(response, "cross_site_request");
  });
});

describe("same-origin requests", () => {
  it("forwards a same-origin POST with the identity attached and the body intact", async () => {
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { ...SAME_ORIGIN, "content-type": "application/json" },
      body: JSON.stringify({ basis: "blank" }),
    });

    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
    expect(forwarded[0].method).toBe("POST");
    expect(forwarded[0].path).toBe("/api/v1/profile/versions");
    expect(forwarded[0].headers.get("x-dev-user-email")).toBeTruthy();
    expect(forwarded[0].headers.get("content-type")).toBe("application/json");
    expect(forwarded[0].body).toBe(JSON.stringify({ basis: "blank" }));
  });

  it("accepts a matching Origin alone, from a browser without Sec-Fetch-Site", async () => {
    const response = await call(POST, ["resumes", "7", "reprocess"], {
      method: "POST",
      headers: { host: "127.0.0.1:3100", origin: "http://127.0.0.1:3100" },
    });
    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
  });

  it("accepts Sec-Fetch-Site: same-origin alone", async () => {
    const response = await call(POST, ["jobs", "7", "reprocess"], {
      method: "POST",
      headers: { host: "localhost:3000", "sec-fetch-site": "same-origin" },
    });
    expect(response.status).toBe(200);
  });

  it("forwards a GET that carries no Origin, as a test runner's request does", async () => {
    const response = await call(GET, ["tailored-resumes", "3", "artifact"], {
      headers: { host: "127.0.0.1:3101" },
    });
    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
  });
});

describe("the EasyApply Chrome extension", () => {
  // What Chrome actually sends for a fetch from an extension's service worker
  // holding host permission for this server — recorded from a real Chromium,
  // not assumed. The id is the one apps/extension/src/manifest.json pins.
  const EXTENSION_ID = "jjhciekcmhmngciccfgignmmlgjknakk";
  const FROM_EXTENSION = {
    host: "localhost:3000",
    origin: `chrome-extension://${EXTENSION_ID}`,
    "sec-fetch-site": "none",
    "content-type": "application/json",
  };
  const JOB = JSON.stringify({ source_type: "greenhouse", source_url: "https://job-boards.greenhouse.io/a/jobs/1", job_title: "Engineer" });

  it("is refused by default — no extension may write until one is named", async () => {
    const response = await call(POST, ["jobs"], { method: "POST", headers: FROM_EXTENSION, body: JOB });
    await expectRefused(response, "cross_site_request");
  });

  it("may save a job once named, with the server's identity attached and the body intact", async () => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["jobs"], { method: "POST", headers: FROM_EXTENSION, body: JOB });

    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
    expect(forwarded[0].method).toBe("POST");
    expect(forwarded[0].path).toBe("/api/v1/jobs");
    expect(forwarded[0].headers.get("x-dev-user-email")).toBeTruthy();
    expect(forwarded[0].headers.get("origin")).toBeNull();
    expect(forwarded[0].body).toBe(JOB);
  });

  it("accepts the id among others, whatever the spacing or case", async () => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", ` not-an-id , ${EXTENSION_ID.toUpperCase()} ,, `);
    const response = await call(POST, ["jobs"], { method: "POST", headers: FROM_EXTENSION, body: JOB });
    expect(response.status).toBe(200);
  });

  it("refuses an extension that is not the one named", async () => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", "abcdefghijklmnopabcdefghijklmnop");
    const response = await call(POST, ["jobs"], { method: "POST", headers: FROM_EXTENSION, body: JOB });
    await expectRefused(response, "cross_site_request");
  });

  it.each([
    ["jobs", "7", "matches"],
    ["jobs", "7", "tailored-resumes"],
    ["jobs", "123456789012345678", "matches"],
  ])("may request a match and start a tailored resume once named: POST /%s/%s/%s", async (...path) => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const body = JSON.stringify({ profile_version_id: 3 });
    const response = await call(POST, path, { method: "POST", headers: FROM_EXTENSION, body });

    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
    expect(forwarded[0].path).toBe(`/api/v1/${path.join("/")}`);
    expect(forwarded[0].headers.get("x-dev-user-email")).toBeTruthy();
    expect(forwarded[0].headers.get("origin")).toBeNull();
    expect(forwarded[0].body).toBe(body);
  });

  it.each([
    [POST, "POST", ["profile", "versions"]],
    [POST, "POST", ["profile", "versions", "2", "confirm"]],
    [POST, "POST", ["profile", "versions", "2", "make-current"]],
    [POST, "POST", ["profile"]],
    [POST, "POST", ["resumes"]],
    [POST, "POST", ["resumes", "7", "reprocess"]],
    [POST, "POST", ["jobs", "7", "reprocess"]],
    [POST, "POST", ["jobs", "7", "match"]],
    [POST, "POST", ["jobs", "7", "matches", "1"]],
    [POST, "POST", ["jobs", "7", "tailored-resumes", "extra"]],
    [POST, "POST", ["jobs", "07", "matches"]],
    [POST, "POST", ["jobs", "0", "matches"]],
    [POST, "POST", ["jobs", "-7", "matches"]],
    [POST, "POST", ["jobs", "7x", "tailored-resumes"]],
    [POST, "POST", ["jobs", "7/../../profile", "matches"]],
    [POST, "POST", ["jobs", "1234567890123456789", "matches"]],
    [POST, "POST", ["matches", "7"]],
    [POST, "POST", ["tailored-resumes", "7"]],
    [POST, "POST", ["tailored-resumes", "7", "artifact"]],
    [POST, "POST", ["internal", "resumes", "7", "retry"]],
    [PUT, "PUT", ["jobs"]],
    [PUT, "PUT", ["jobs", "7", "matches"]],
    [PUT, "PUT", ["jobs", "7", "tailored-resumes"]],
    [PUT, "PUT", ["profile", "versions", "2", "snapshot"]],
  ])("may not write anything outside its workflow (%s %s)", async (handler, method, path) => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(handler, path, { method, headers: FROM_EXTENSION, body: "{}" });
    await expectRefused(response, "cross_site_request");
  });

  it.each([["jobs", "7", "matches"], ["jobs", "7", "tailored-resumes"]])(
    "is refused on POST /%s/%s/%s by default, and when a page caused it",
    async (...path) => {
      let response = await call(POST, path, { method: "POST", headers: FROM_EXTENSION, body: "{}" });
      await expectRefused(response, "cross_site_request");
      vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
      response = await call(POST, path, {
        method: "POST",
        headers: { ...FROM_EXTENSION, "sec-fetch-site": "cross-site" },
        body: "{}",
      });
      await expectRefused(response, "cross_site_request");
    },
  );

  it("leaves one structured line per extension request, with no identity and no body", async () => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    vi.stubEnv("DEV_USER_EMAIL", "owner@easyapply.test");
    const lines: string[] = [];
    const info = vi.spyOn(console, "info").mockImplementation((line: string) => void lines.push(line));
    await call(POST, ["jobs", "7", "matches"], { method: "POST", headers: FROM_EXTENSION, body: JSON.stringify({ profile_version_id: 3 }) });
    await call(POST, ["profile", "versions"], { method: "POST", headers: FROM_EXTENSION, body: "{}" });
    await call(GET, ["jobs", "7", "requirements"], { headers: { host: "localhost:3000", origin: FROM_EXTENSION.origin } });
    await call(GET, ["jobs"], { headers: SAME_ORIGIN });
    info.mockRestore();

    expect(lines).toHaveLength(3);
    const logged = lines.map((line) => JSON.parse(line.replace(/^proxy\.extension /, "")));
    expect(logged.map(({ method, route, status }) => [method, route, status])).toEqual([
      ["POST", "/jobs/:id/matches", 200],
      ["POST", "/profile/versions", 403],
      ["GET", "/jobs/:id/requirements", 200],
    ]);
    expect(logged[0].extension).toBe(EXTENSION_ID);
    expect(typeof logged[0].ms).toBe("number");
    for (const line of lines) {
      expect(line).not.toContain("owner@easyapply.test");
      expect(line).not.toContain("profile_version_id");
    }
  });

  it.each(["cross-site", "same-site", "same-origin"])(
    "is refused when Sec-Fetch-Site says %s — a page caused it, not the extension",
    async (site) => {
      vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
      const response = await call(POST, ["jobs"], {
        method: "POST",
        headers: { ...FROM_EXTENSION, "sec-fetch-site": site },
        body: JOB,
      });
      await expectRefused(response, "cross_site_request");
    },
  );

  it.each([
    `chrome-extension://${EXTENSION_ID}/`,
    `chrome-extension://${EXTENSION_ID}.evil.example`,
    `CHROME-EXTENSION://${EXTENSION_ID}`,
    `moz-extension://${EXTENSION_ID}`,
    `https://${EXTENSION_ID}`,
    "null",
  ])("does not mistake %j for the extension's origin", async (origin) => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["jobs"], {
      method: "POST",
      headers: { ...FROM_EXTENSION, origin },
      body: JOB,
    });
    await expectRefused(response, "cross_site_request");
  });

  it("still refuses a foreign Host, before looking at the origin at all", async () => {
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["jobs"], {
      method: "POST",
      headers: { ...FROM_EXTENSION, host: "evil.example:3000" },
      body: JOB,
    });
    await expectRefused(response, "host_not_allowed");
  });
});

describe("the Host header", () => {
  it.each(["localhost:3000", "localhost", "127.0.0.1:3100", "[::1]:3000", "LOCALHOST:3000"])(
    "answers loopback host %s",
    async (host) => {
      const response = await call(GET, ["profile"], { headers: { host } });
      expect(response.status).toBe(200);
    },
  );

  it("refuses a rebound hostname even when the request looks same-origin", async () => {
    // DNS rebinding: evil.example now resolves to 127.0.0.1, so to the browser
    // the attacker's page IS this origin, and both headers say so.
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: {
        host: "evil.example:3000",
        origin: "http://evil.example:3000",
        "sec-fetch-site": "same-origin",
      },
    });
    await expectRefused(response, "host_not_allowed");
  });

  it("refuses a rebound hostname on reads too", async () => {
    const response = await call(GET, ["profile"], { headers: { host: "evil.example:3000" } });
    await expectRefused(response, "host_not_allowed");
  });

  it.each(["localhost@evil.example", "evil.example/localhost", "localhost:3000/x", "localhost.", ""])(
    "refuses a malformed or disguised host %j",
    async (host) => {
      const response = await call(GET, ["profile"], { headers: { host } });
      await expectRefused(response, "host_not_allowed");
    },
  );

  it("answers a host named in PROXY_ALLOWED_HOSTS, on any port, and only then", async () => {
    const before = await call(GET, ["profile"], { headers: { host: "easyapply.internal:8443" } });
    await expectRefused(before, "host_not_allowed");

    vi.stubEnv("PROXY_ALLOWED_HOSTS", " Other.Example , easyapply.internal ");
    const after = await call(GET, ["profile"], { headers: { host: "easyapply.internal:8443" } });
    expect(after.status).toBe(200);

    const unlisted = await call(GET, ["profile"], { headers: { host: "third.example" } });
    expect(unlisted.status).toBe(403);
  });
});

describe("identity: who the forwarded request is for", () => {
  const SECRET = "a-development-session-secret-of-32+chars";
  const EXTENSION_ID = "jjhciekcmhmngciccfgignmmlgjknakk";
  const FROM_EXTENSION = {
    host: "localhost:3000",
    origin: `chrome-extension://${EXTENSION_ID}`,
    "sec-fetch-site": "none",
    "content-type": "application/json",
  };

  async function sessionCookieFor(email: string, secret = SECRET): Promise<string> {
    const { signSession, nowSeconds } = await import("@/server/auth/session-token");
    const now = nowSeconds();
    return `easyapply_session=${await signSession({ sub: email, iat: now, exp: now + 600 }, secret)}`;
  }

  function devSession() {
    vi.stubEnv("AUTH_MODE", "dev-session");
    vi.stubEnv("SESSION_SECRET", SECRET);
  }

  it("development: the configured identity, and nothing the client sends changes it", async () => {
    vi.stubEnv("DEV_USER_EMAIL", "configured@easyapply.test");
    const response = await call(GET, ["profile"], {
      headers: {
        host: "localhost:3000",
        "x-dev-user-email": "someone-else@easyapply.test",
        authorization: "Bearer stolen",
        cookie: await sessionCookieFor("session-user@easyapply.test"),
      },
    });
    expect(response.status).toBe(200);
    expect(forwarded[0].headers.get("x-dev-user-email")).toBe("configured@easyapply.test");
    expect(forwarded[0].headers.get("authorization")).toBeNull();
    expect(forwarded[0].headers.get("cookie")).toBeNull();
  });

  it("dev-session: no session is a 401 answered here, never forwarded", async () => {
    devSession();
    const response = await call(GET, ["profile"], { headers: { host: "localhost:3000" } });
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "You are not signed in to EasyApply.", code: "session_required" });
    expect(forwarded).toEqual([]);
  });

  it("dev-session: a session is forwarded as its person — and a claimed header is not", async () => {
    devSession();
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: {
        ...SAME_ORIGIN,
        "content-type": "application/json",
        "x-dev-user-email": "victim@easyapply.test",
        cookie: await sessionCookieFor("owner@easyapply.test"),
      },
      body: "{}",
    });
    expect(response.status).toBe(200);
    expect(forwarded[0].headers.get("x-dev-user-email")).toBe("owner@easyapply.test");
    expect(forwarded[0].headers.get("cookie")).toBeNull();
  });

  it("dev-session: a cookie signed by another server's secret is no session", async () => {
    devSession();
    const response = await call(GET, ["jobs"], {
      headers: { host: "localhost:3000", cookie: await sessionCookieFor("owner@easyapply.test", "another-secret-that-is-32-chars-long!!") },
    });
    expect(response.status).toBe(401);
    expect(forwarded).toEqual([]);
  });

  it("dev-session: the extension saves as whoever the browser's session is", async () => {
    devSession();
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["jobs"], {
      method: "POST",
      headers: { ...FROM_EXTENSION, cookie: await sessionCookieFor("owner@easyapply.test") },
      body: "{}",
    });
    expect(response.status).toBe(200);
    expect(forwarded[0].path).toBe("/api/v1/jobs");
    expect(forwarded[0].headers.get("x-dev-user-email")).toBe("owner@easyapply.test");
  });

  it.each([["jobs", "7", "matches"], ["jobs", "7", "tailored-resumes"]])(
    "dev-session: the extension's POST /%s/%s/%s is the browser's session's — a claimed header changes nothing",
    async (...path) => {
      devSession();
      vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
      const response = await call(POST, path, {
        method: "POST",
        headers: {
          ...FROM_EXTENSION,
          "x-dev-user-email": "victim@easyapply.test",
          authorization: "Bearer someone-elses",
          cookie: await sessionCookieFor("owner@easyapply.test"),
        },
        body: JSON.stringify({ profile_version_id: 3 }),
      });
      expect(response.status).toBe(200);
      expect(forwarded[0].path).toBe(`/api/v1/${path.join("/")}`);
      expect(forwarded[0].headers.get("x-dev-user-email")).toBe("owner@easyapply.test");
      expect(forwarded[0].headers.get("authorization")).toBeNull();
      expect(forwarded[0].headers.get("cookie")).toBeNull();
    },
  );

  it.each([["jobs", "7", "matches"], ["jobs", "7", "tailored-resumes"]])(
    "dev-session: the extension's POST /%s/%s/%s with no session is told to sign in, never forwarded",
    async (...path) => {
      devSession();
      vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
      const response = await call(POST, path, { method: "POST", headers: FROM_EXTENSION, body: "{}" });
      expect(response.status).toBe(401);
      expect((await response.json()).code).toBe("session_required");
      expect(forwarded).toEqual([]);
    },
  );

  it("dev-session: the extension with no session is told to sign in, not saved as anyone", async () => {
    devSession();
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["jobs"], { method: "POST", headers: FROM_EXTENSION, body: "{}" });
    expect(response.status).toBe(401);
    expect((await response.json()).code).toBe("session_required");
    expect(forwarded).toEqual([]);
  });

  it("admission comes first: a cross-site write is refused as cross-site, session or not", async () => {
    devSession();
    for (const cookie of [undefined, await sessionCookieFor("owner@easyapply.test")]) {
      const response = await call(POST, ["profile", "versions"], {
        method: "POST",
        headers: {
          host: "localhost:3000",
          origin: "https://evil.example",
          "sec-fetch-site": "cross-site",
          ...(cookie ? { cookie } : {}),
        },
      });
      await expectRefused(response, "cross_site_request");
    }
  });

  it("a session does not widen what the extension may write", async () => {
    devSession();
    vi.stubEnv("PROXY_ALLOWED_EXTENSION_IDS", EXTENSION_ID);
    const response = await call(POST, ["profile", "versions"], {
      method: "POST",
      headers: { ...FROM_EXTENSION, cookie: await sessionCookieFor("owner@easyapply.test") },
      body: "{}",
    });
    await expectRefused(response, "cross_site_request");
  });

  it("a misconfigured server forwards nothing and names no identity", async () => {
    vi.stubEnv("AUTH_MODE", "entra");
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await call(GET, ["profile"], { headers: { host: "localhost:3000" } });
    expect(response.status).toBe(500);
    expect((await response.json()).code).toBe("auth_misconfigured");
    expect(forwarded).toEqual([]);
    error.mockRestore();
  });
});
