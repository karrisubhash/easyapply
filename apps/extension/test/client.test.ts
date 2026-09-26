import { apiRequest, backendUrl } from "../src/api/client";
import { isRequirementsResult } from "../src/api/guards";

const WEB = "http://localhost:3000";
const accept = (value: unknown): value is { id: number } =>
  typeof value === "object" && value !== null && typeof (value as { id?: unknown }).id === "number";

function answering(status: number, body: unknown, contentType = "application/json") {
  const seen: RequestInit[] = [];
  const fetch = async (_input: RequestInfo | URL, init: RequestInit = {}) => {
    seen.push(init);
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": contentType } });
  };
  return { fetch: fetch as typeof globalThis.fetch, seen };
}

describe("the backend transport", () => {
  it("goes to the web app's proxy, never the API", () => {
    expect(backendUrl(WEB, "/jobs/12/requirements")).toBe("http://localhost:3000/api/backend/jobs/12/requirements");
  });

  it("sends the browser's session and nothing that says who this is", async () => {
    const { fetch, seen } = answering(200, { id: 1 });
    await apiRequest({ method: "POST", path: "/jobs/1/matches", body: { profile_version_id: 3 }, accept }, { webUrl: WEB, sessionTag: null, fetch });
    const [init] = seen;
    expect(init?.credentials).toBe("include");
    expect(init?.redirect).toBe("error");
    expect(init?.cache).toBe("no-store");
    const headers = new Headers(init?.headers);
    expect([...headers.keys()].sort()).toEqual(["accept", "content-type"]);
    expect(JSON.parse(String(init?.body))).toEqual({ profile_version_id: 3 });
  });

  it("adds the account's tag, when known, as X-EasyApply-Session — on reads and writes alike", async () => {
    for (const method of ["GET", "POST"] as const) {
      const { fetch, seen } = answering(200, { id: 1 });
      await apiRequest({ method, path: "/jobs/1/matches", body: {}, accept }, { webUrl: WEB, sessionTag: "Tag_of-A1", fetch });
      const headers = new Headers(seen[0]?.headers);
      expect(headers.get("X-EasyApply-Session")).toBe("Tag_of-A1");
      expect([...headers.keys()].sort()).toEqual(
        method === "POST" ? ["accept", "content-type", "x-easyapply-session"] : ["accept", "x-easyapply-session"],
      );
    }
  });

  it("sends no body on a GET", async () => {
    const { fetch, seen } = answering(200, { id: 1 });
    await apiRequest({ method: "GET", path: "/matches/1", accept }, { webUrl: WEB, sessionTag: null, fetch });
    expect(seen[0]?.body).toBeUndefined();
    expect(seen[0]?.method).toBe("GET");
  });

  it("returns the answer when it is EasyApply's shape", async () => {
    const { fetch } = answering(201, { id: 7 });
    expect(await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch })).toEqual({ ok: true, status: 201, data: { id: 7 } });
  });

  it("refuses a 200 that is not EasyApply's shape", async () => {
    const { fetch } = answering(200, "<html>a captive portal</html>", "text/html");
    const result = await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch });
    expect(result).toMatchObject({ ok: false, kind: "unexpected_response", status: 200 });
  });

  it.each([
    [401, { detail: "You are not signed in to EasyApply.", code: "session_required" }, "session_required"],
    [404, { detail: "That job does not exist.", code: "not_found" }, "not_found"],
    [409, { detail: "This job has not been structured yet.", code: "conflict" }, "conflict"],
    [409, { detail: "Signed in as someone else now.", code: "session_changed" }, "session_changed"],
    [409, { code: "session_changed" }, "session_changed"],
    [403, { detail: "Cross-site requests are not accepted.", code: "cross_site_request" }, "refused"],
    [401, { detail: "Not identified.", code: "unauthenticated" }, "refused"],
    [500, { detail: "Sign-in is misconfigured on this server.", code: "auth_misconfigured" }, "refused"],
    [502, { detail: "The EasyApply API is unreachable.", code: "api_unreachable" }, "api_unreachable"],
    [422, { detail: [{ loc: ["body", "x"], msg: "extra", type: "extra_forbidden" }] }, "refused"],
  ])("classifies %i %j as %s", async (status, body, kind) => {
    const { fetch } = answering(status, body);
    const result = await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch });
    expect(result).toMatchObject({ ok: false, kind, status });
  });

  it("does not mistake the API's own 401 for a session the person can fix", async () => {
    const { fetch } = answering(401, { detail: "Not identified.", code: "unauthenticated" });
    const result = await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch });
    expect(result).toMatchObject({ kind: "refused", code: "unauthenticated" });
  });

  it("reports an answer with no reason as unexpected, not as a refusal it cannot explain", async () => {
    const { fetch } = answering(500, "Internal Server Error", "text/plain");
    expect(await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch })).toMatchObject({
      ok: false,
      kind: "unexpected_response",
      status: 500,
    });
  });

  it("reports nothing answering as a network failure", async () => {
    const fetch = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof globalThis.fetch;
    expect(await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch })).toMatchObject({
      ok: false,
      kind: "network",
      status: null,
    });
  });

  it("gives up after its timeout and says so", async () => {
    const fetch = ((_input: RequestInfo | URL, init: RequestInit = {}) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      })) as typeof globalThis.fetch;
    const result = await apiRequest({ method: "GET", path: "/x", accept }, { webUrl: WEB, sessionTag: null, fetch, timeoutMs: 20 });
    expect(result).toMatchObject({ ok: false, kind: "timeout" });
  });
});

describe("the requirements answer's shape check", () => {
  const job = { id: 1, latest_run: null };
  it("accepts a job still being read (no requirements yet)", () => {
    expect(isRequirementsResult({ job, run: null, requirements: null })).toBe(true);
  });
  it("refuses something that is not a job", () => {
    expect(isRequirementsResult({ job: { id: "1" }, run: null, requirements: null })).toBe(false);
    expect(isRequirementsResult({ run: null, requirements: null })).toBe(false);
    expect(isRequirementsResult([])).toBe(false);
  });
  it("refuses a reading with no candidate", () => {
    expect(isRequirementsResult({ job, run: null, requirements: { structurer: "deterministic" } })).toBe(false);
  });
});
