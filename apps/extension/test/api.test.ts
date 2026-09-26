import { isSubmitOutcome, jobPageUrl, jobsEndpoint, submitJob } from "../src/api/jobs";
import type { JobIngestionRequest } from "../src/contract";
import { createFakeApi } from "./support/fake-api";
import { JOB_URL } from "./support/fixtures";

const WEB = "http://localhost:3000";

const PAYLOAD: JobIngestionRequest = {
  source_type: "greenhouse",
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: "Build things.",
};

describe("where requests go", () => {
  it("saves through the web app's proxy, which is where identity is attached", () => {
    expect(jobsEndpoint(WEB)).toBe("http://localhost:3000/api/backend/jobs");
    expect(jobPageUrl(WEB, 42)).toBe("http://localhost:3000/jobs/42");
  });
});

describe("submitJob — the request", () => {
  it("is one JSON POST carrying the payload, presenting the browser's session and no identity of its own", async () => {
    const api = createFakeApi();
    await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch });

    expect(api.requests).toHaveLength(1);
    const [request] = api.requests;
    expect(request?.url).toBe("http://localhost:3000/api/backend/jobs");
    expect(request?.method).toBe("POST");
    expect(request?.headers).toEqual({ "content-type": "application/json", accept: "application/json" });
    // The browser attaches the web app's session cookie; the extension never sees it.
    expect(request?.credentials).toBe("include");
    expect(request?.redirect).toBe("error");
    expect(request?.body).toBe(JSON.stringify(PAYLOAD));
  });

  it("carries the tag of the account it is for, when the web app gave one — and nothing else new", async () => {
    const api = createFakeApi();
    const tag = api.currentTag!;
    await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: tag, fetch: api.fetch });
    expect(api.requests[0]?.headers).toEqual({
      "content-type": "application/json",
      accept: "application/json",
      "x-easyapply-session": tag,
    });
  });
});

describe("submitJob — the answers", () => {
  it("201 is a new job", async () => {
    const api = createFakeApi();
    const outcome = await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch });
    expect(outcome.kind).toBe("created");
    expect(outcome.kind === "created" && outcome.job.id).toBe(1);
  });

  it("200 is the job EasyApply already had — the API's idempotency, reported as such", async () => {
    const api = createFakeApi();
    await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch });
    const again = await submitJob({ ...PAYLOAD, job_title: "Edited" }, { webUrl: WEB, sessionTag: null, fetch: api.fetch });
    expect(again.kind).toBe("existing");
    expect(again.kind === "existing" && again.job.job_title).toBe("Senior Backend Engineer");
    expect(api.jobs).toHaveLength(1);
  });

  it("a field-level 422 lists what was refused", async () => {
    const api = createFakeApi();
    api.respondNextWith({
      status: 422,
      body: JSON.stringify({
        detail: [{ loc: ["body", "job_title"], msg: "String should have at most 500 characters", type: "string_too_long" }],
      }),
    });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toEqual({
      kind: "rejected",
      status: 422,
      code: "request_validation",
      message: "EasyApply refused some of the fields.",
      issues: [{ loc: ["body", "job_title"], msg: "String should have at most 500 characters", type: "string_too_long" }],
    });
  });

  it.each([
    [403, "cross_site_request", "Cross-site requests are not accepted."],
    [403, "host_not_allowed", "This server does not answer for that host."],
    [401, "unauthenticated", "Development identity is disabled in this environment, and no other sign-in exists yet."],
    [401, "session_required", "You are not signed in to EasyApply."],
  ])("%d %s is a refusal carrying the server's own words", async (status, code, detail) => {
    const api = createFakeApi();
    api.respondNextWith({ status, body: JSON.stringify({ detail, code }) });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toEqual({
      kind: "rejected",
      status,
      code,
      message: detail,
      issues: [],
    });
  });

  it("a save for an account the browser has since left is refused by the proxy, and says so — nothing is saved", async () => {
    const api = createFakeApi({ session: { mode: "dev-session", email: "a@example.test" } });
    const aTag = api.currentTag;
    api.setSession({ mode: "dev-session", email: "b@example.test" });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: aTag, fetch: api.fetch })).toEqual({
      kind: "rejected",
      status: 409,
      code: "session_changed",
      message: "This browser is signed in to EasyApply as a different account now.",
      issues: [],
    });
    expect(api.jobs).toHaveLength(0);
  });

  it("the same person signed in again keeps the tag, so the save goes through", async () => {
    const api = createFakeApi({ session: { mode: "dev-session", email: "a@example.test" } });
    const tag = api.currentTag;
    api.setSession(null);
    api.setSession({ mode: "dev-session", email: "a@example.test" });
    expect((await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: tag, fetch: api.fetch })).kind).toBe("created");
  });

  it.each([
    [401, "session_required"],
    [409, "session_changed"],
  ])("a %d %s without a sentence is still told apart from any other refusal", async (status, code) => {
    const api = createFakeApi();
    api.respondNextWith({ status, body: JSON.stringify({ code }) });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toMatchObject({
      kind: "rejected",
      status,
      code,
    });
  });

  it("an ordinary 409 is not mistaken for a session change", async () => {
    const api = createFakeApi();
    api.respondNextWith({ status: 409, body: JSON.stringify({ detail: "Busy.", code: "conflict" }) });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toMatchObject({ code: "conflict" });
  });

  it("the proxy's 502 means the API is down", async () => {
    const api = createFakeApi();
    api.respondNextWith({
      status: 502,
      body: JSON.stringify({ detail: "The EasyApply API is unreachable.", code: "api_unreachable" }),
    });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toEqual({
      kind: "unreachable",
      code: "api_unreachable",
      message: "The EasyApply API is unreachable.",
    });
  });

  it("nothing listening is a network failure", async () => {
    const api = createFakeApi();
    api.respondNextWith("network-error");
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toEqual({
      kind: "unreachable",
      code: "network",
      message: "Could not reach EasyApply at http://localhost:3000.",
    });
  });

  it("a 200 that is not a job is somebody else answering", async () => {
    const api = createFakeApi();
    api.respondNextWith({ status: 200, body: "<html>Another app</html>", contentType: "text/html" });
    const outcome = await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch });
    expect(outcome).toMatchObject({ kind: "unreachable", code: "unexpected_response" });
  });

  it("a 500 with an HTML body is still a refusal, not a crash", async () => {
    const api = createFakeApi();
    api.respondNextWith({ status: 500, body: "<html>Internal Server Error</html>", contentType: "text/html" });
    expect(await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: api.fetch })).toEqual({
      kind: "rejected",
      status: 500,
      code: "unexpected_response",
      message: "EasyApply answered 500 without saying why.",
      issues: [],
    });
  });

  it("gives up after the timeout, and says a retry is safe", async () => {
    const hanging: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const outcome = await submitJob(PAYLOAD, { webUrl: WEB, sessionTag: null, fetch: hanging, timeoutMs: 20 });
    expect(outcome.kind).toBe("unreachable");
    expect(outcome.kind === "unreachable" && outcome.code).toBe("timeout");
    expect(outcome.kind === "unreachable" && outcome.message).toMatch(/saving again is safe/);
  });
});

describe("isSubmitOutcome — what the popup accepts from the worker", () => {
  it.each([
    [{ kind: "created", job: { id: 1 } }, true],
    [{ kind: "existing", job: { id: 1 } }, true],
    [{ kind: "rejected", status: 403, code: "x", message: "No.", issues: [] }, true],
    [{ kind: "unreachable", code: "network", message: "Down." }, true],
    [undefined, false],
    [null, false],
    [{ kind: "created" }, false],
    [{ kind: "saved", job: {} }, false],
    [{ kind: "rejected" }, false],
  ])("%j -> %s", (value, expected) => {
    expect(isSubmitOutcome(value)).toBe(expected);
  });
});
