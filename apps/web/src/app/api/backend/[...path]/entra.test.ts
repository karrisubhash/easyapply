// @vitest-environment node

/**
 * The proxy under real sign-in, and the session-consistency check in every
 * mode: what goes on the wire to the API (msw at the network boundary), and
 * what is answered here without anything going there at all.
 */

import { SESSION_TAG_HEADER } from "@easyapply/shared";
import { jwtVerify } from "jose";
import { http, HttpResponse } from "msw";
import { NextRequest } from "next/server";

import { signEntraSession, newSessionId, type EntraSessionClaims } from "@/server/auth/entra-session";
import { accountTag, SESSION_COOKIE } from "@/server/auth/identity";
import { nowSeconds, signSession } from "@/server/auth/session-token";
import { ASSERTION_SECRET, CLIENT_ID, ISSUER, OID_A, OID_B, SESSION_SECRET, stubEntraEnv, TENANT } from "@/test/entra";
import { server } from "@/test/server";

import { GET, POST } from "./route";

const UPSTREAM = "http://127.0.0.1:8000/api/v1";
const SAME_ORIGIN = { host: "localhost:3000", origin: "http://localhost:3000", "sec-fetch-site": "same-origin" };
const DEV_SECRET = "a-development-session-secret-of-32+chars";

type Forwarded = { path: string; headers: Headers };
let forwarded: Forwarded[] = [];

beforeEach(() => {
  forwarded = [];
  server.use(
    http.all(`${UPSTREAM}/*`, ({ request }) => {
      forwarded.push({ path: new URL(request.url).pathname, headers: request.headers });
      return HttpResponse.json({ ok: true });
    }),
  );
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function call(handler: typeof GET, path: string[], { method = "GET", headers = {} }: { method?: string; headers?: Record<string, string> } = {}) {
  const request = new NextRequest(`http://localhost:3000/api/backend/${path.join("/")}`, {
    method,
    headers,
    body: method === "GET" ? undefined : "{}",
  });
  return handler(request, { params: Promise.resolve({ path }) });
}

async function entraCookie(overrides: Partial<EntraSessionClaims> = {}): Promise<string> {
  const iat = nowSeconds();
  const token = await signEntraSession(
    { typ: "entra", iss: ISSUER, aud: CLIENT_ID, tid: TENANT, oid: OID_A, email: "ada@example.test", name: "Ada", op: false, sid: newSessionId(), iat, exp: iat + 3600, ...overrides },
    SESSION_SECRET,
  );
  return `${SESSION_COOKIE}=${token}`;
}

async function assertionOf(headers: Headers) {
  const bearer = headers.get("authorization") ?? "";
  expect(bearer).toMatch(/^Bearer /);
  return (await jwtVerify(bearer.slice(7), new TextEncoder().encode(ASSERTION_SECRET), { algorithms: ["HS256"] })).payload;
}

describe("entra: who the API is told the request is for", () => {
  beforeEach(() => stubEntraEnv({ OPERATOR_API_TOKEN: "" }));

  it("forwards a signed, single-use assertion of the session's person — and nothing the client claimed", async () => {
    const response = await call(GET, ["profile"], {
      headers: {
        host: "localhost:3000",
        cookie: await entraCookie(),
        authorization: "Bearer forged-by-the-client",
        "x-dev-user-email": "someone-else@example.test",
        "x-user-id": "7",
      },
    });
    expect(response.status).toBe(200);
    expect(forwarded).toHaveLength(1);
    const headers = forwarded[0]!.headers;
    expect(headers.get("x-dev-user-email")).toBeNull();
    expect(headers.get("x-user-id")).toBeNull();
    expect(headers.get("cookie")).toBeNull();
    const claims = await assertionOf(headers);
    expect(claims).toMatchObject({ iss: "easyapply-web", aud: "easyapply-api", sub: OID_A, tid: TENANT, idp: ISSUER });
  });

  it("without a session answers 401 session_required, and forwards nothing", async () => {
    const response = await call(GET, ["profile"], { headers: { host: "localhost:3000" } });
    expect(response.status).toBe(401);
    expect((await response.json()).code).toBe("session_required");
    expect(forwarded).toEqual([]);
  });

  it("a session signed with another secret, or edited, is no session", async () => {
    const iat = nowSeconds();
    const foreign = await signEntraSession(
      { typ: "entra", iss: ISSUER, aud: CLIENT_ID, tid: TENANT, oid: OID_A, email: null, name: null, op: true, sid: newSessionId(), iat, exp: iat + 60 },
      "a-different-secret-of-at-least-32-characters",
    );
    for (const cookie of [`${SESSION_COOKIE}=${foreign}`, `${SESSION_COOKIE}=v2.e30.AAAA`]) {
      expect((await call(GET, ["profile"], { headers: { host: "localhost:3000", cookie } })).status).toBe(401);
    }
    expect(forwarded).toEqual([]);
  });

  it("a development session is not a real one", async () => {
    const v1 = await signSession({ sub: "ada@example.test", iat: nowSeconds(), exp: nowSeconds() + 60 }, SESSION_SECRET);
    const response = await call(GET, ["profile"], { headers: { host: "localhost:3000", cookie: `${SESSION_COOKIE}=${v1}` } });
    expect(response.status).toBe(401);
    expect(forwarded).toEqual([]);
  });
});

describe("entra: the operator pages", () => {
  beforeEach(() => stubEntraEnv({ OPERATOR_API_TOKEN: "operator-token-for-tests" }));

  it("are a 404 for an ordinary account, with nothing forwarded and no token anywhere", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const response = await call(GET, ["internal", "resume-processing", "attention"], {
      headers: { host: "localhost:3000", cookie: await entraCookie({ op: false }) },
    });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "Not Found" });
    expect(forwarded).toEqual([]);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("proxy.operator_refused"));
  });

  it("are forwarded for an operator, with the token and the role in the assertion", async () => {
    await call(GET, ["internal", "resume-processing", "attention"], {
      headers: { host: "localhost:3000", cookie: await entraCookie({ op: true }) },
    });
    expect(forwarded).toHaveLength(1);
    expect(forwarded[0]!.headers.get("x-operator-token")).toBe("operator-token-for-tests");
    expect((await assertionOf(forwarded[0]!.headers)).roles).toEqual(["EasyApply.Operator"]);
  });

  it.each([
    [["jobs", "../internal", "resume-processing", "attention"]],
    [["..", "internal", "resume-processing", "attention"]],
    [["jobs", "..", "..", "..", "openapi.json"]],
    [["jobs", "1/../../internal"]],
    [["jobs", "a\\b"]],
    [["jobs", "."]],
    [["jobs", ""]],
  ])("a path that decodes to something else (%j) is a 404, and nothing is forwarded", async (path) => {
    const response = await call(GET, path, { headers: { host: "localhost:3000", cookie: await entraCookie({ op: false }) } });
    expect(response.status).toBe(404);
    expect(forwarded).toEqual([]);
  });

  it("never put the operator token on ordinary traffic, operator or not", async () => {
    await call(GET, ["profile"], { headers: { host: "localhost:3000", cookie: await entraCookie({ op: true }) } });
    expect(forwarded[0]!.headers.get("x-operator-token")).toBeNull();
  });
});

describe("the account tag (every mode)", () => {
  it("dev-session: a request rendered for another account is 409 session_changed, never forwarded", async () => {
    vi.stubEnv("AUTH_MODE", "dev-session");
    vi.stubEnv("SESSION_SECRET", DEV_SECRET);
    const b = await signSession({ sub: "b@example.test", iat: nowSeconds(), exp: nowSeconds() + 60 }, DEV_SECRET);
    const tagOfA = await accountTag("dev-session", "a@example.test");

    const response = await call(POST, ["jobs"], {
      method: "POST",
      headers: { ...SAME_ORIGIN, "content-type": "application/json", cookie: `${SESSION_COOKIE}=${b}`, [SESSION_TAG_HEADER]: tagOfA },
    });
    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("session_changed");
    expect(forwarded).toEqual([]);

    // The tag of the account the cookie is for goes through.
    const ok = await call(GET, ["jobs"], {
      headers: { host: "localhost:3000", cookie: `${SESSION_COOKIE}=${b}`, [SESSION_TAG_HEADER]: await accountTag("dev-session", "b@example.test") },
    });
    expect(ok.status).toBe(200);
  });

  it("signed out is said first: 401, not 409, whatever tag is sent", async () => {
    vi.stubEnv("AUTH_MODE", "dev-session");
    vi.stubEnv("SESSION_SECRET", DEV_SECRET);
    const response = await call(GET, ["jobs"], { headers: { host: "localhost:3000", [SESSION_TAG_HEADER]: "some-old-tag" } });
    expect(response.status).toBe(401);
    expect((await response.json()).code).toBe("session_required");
  });

  it("entra: another person's tag is refused; the same person after signing in again is not", async () => {
    stubEntraEnv();
    const tagOfA = await accountTag("entra", `${TENANT}:${OID_A}`);
    const asB = await call(GET, ["profile"], { headers: { host: "localhost:3000", cookie: await entraCookie({ oid: OID_B }), [SESSION_TAG_HEADER]: tagOfA } });
    expect(asB.status).toBe(409);
    // A new session (new sid, new iat) for the same person: same account, same tag.
    const againAsA = await call(GET, ["profile"], { headers: { host: "localhost:3000", cookie: await entraCookie(), [SESSION_TAG_HEADER]: tagOfA } });
    expect(againAsA.status).toBe(200);
    expect(forwarded).toHaveLength(1);
  });

  it("development: a client that sends no tag is not checked (older clients keep working)", async () => {
    vi.stubEnv("AUTH_MODE", "development");
    vi.stubEnv("DEV_USER_EMAIL", "dev@easyapply.local");
    expect((await call(GET, ["profile"], { headers: { host: "localhost:3000" } })).status).toBe(200);
    expect((await call(GET, ["profile"], { headers: { host: "localhost:3000", [SESSION_TAG_HEADER]: "" } })).status).toBe(409);
  });
});
