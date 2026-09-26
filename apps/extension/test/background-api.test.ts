import { apiRequest } from "../src/api/client";
import { submitJob } from "../src/api/jobs";
import { getSession } from "../src/api/session";
import { createDiagnostics } from "../src/background/diagnostics";
import { createBackground } from "../src/background/service";
import { API, SUBMIT } from "../src/shared/messages";
import { createFakeApi, fakeSessionTag } from "./support/fake-api";
import { EXTENSION_ID } from "./support/fake-chrome";
import { JOB_URL } from "./support/fixtures";

const WEB = "http://localhost:3000";
const POPUP = { id: EXTENSION_ID, url: `chrome-extension://${EXTENSION_ID}/popup.html` };
/** The tag the fake web app gives the configured development account. */
const TAG = fakeSessionTag("dev@easyapply.local");
const PAYLOAD = {
  source_type: "greenhouse" as const,
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: "A private description with ada@example.test in it.",
};

function setup() {
  const api = createFakeApi();
  const lines: string[] = [];
  const diagnostics = createDiagnostics({ log: (line) => lines.push(line) });
  const background = createBackground({
    submit: (payload, sessionTag) => submitJob(payload, { webUrl: WEB, sessionTag, fetch: api.fetch }),
    session: () => getSession({ webUrl: WEB, fetch: api.fetch }),
    request: (prepared, sessionTag) => apiRequest(prepared, { webUrl: WEB, sessionTag, fetch: api.fetch }),
    store: { get: async () => undefined, set: async () => undefined, remove: async () => undefined },
    extensionId: EXTENSION_ID,
    diagnostics,
  });
  return { api, background, lines, diagnostics };
}

describe("backend calls through the worker", () => {
  it("are answered for the popup", async () => {
    const { api, background } = setup();
    await background.handle({ type: SUBMIT, tabId: 1, payload: PAYLOAD, account: "dev@easyapply.local", sessionTag: TAG }, POPUP);
    const answer = await background.handle({ type: API, call: { op: "requirements", jobId: 1 }, sessionTag: TAG }, POPUP);
    expect(answer).toMatchObject({ ok: true, status: 200, data: { job: { id: 1 } } });
    expect(api.requests.at(-1)?.url).toBe(`${WEB}/api/backend/jobs/1/requirements`);
    expect(api.requests.at(-1)?.headers["x-easyapply-session"]).toBe(TAG);
  });

  it.each([
    ["missing", undefined],
    ["not a string", ["tag"]],
    ["empty", ""],
    ["too long", "t".repeat(129)],
    ["not tag-shaped", "a/b"],
  ])("refuse a call whose session tag is %s", (_what, sessionTag) => {
    const { api, background } = setup();
    expect(background.handle({ type: API, call: { op: "profileVersions" }, sessionTag }, POPUP)).toBeNull();
    expect(api.requests).toHaveLength(0);
  });

  it("answer 409 session_changed for a call made for an account the browser has left, and do nothing", async () => {
    const { api, background } = setup();
    api.setSession({ mode: "dev-session", email: "someone@else.test" });
    const answer = await background.handle({ type: API, call: { op: "createMatch", jobId: 1, profileVersionId: 1 }, sessionTag: TAG }, POPUP);
    expect(answer).toMatchObject({ ok: false, kind: "session_changed", status: 409, code: "session_changed" });
    expect(api.matches).toHaveLength(0);
  });

  it.each([
    ["a content script (inside the job page)", { id: EXTENSION_ID, url: "https://job-boards.greenhouse.io/acme/jobs/1" }],
    ["another extension", { id: "abcdefghijklmnopabcdefghijklmnop", url: "chrome-extension://abcdefghijklmnopabcdefghijklmnop/popup.html" }],
    ["a page of this extension that is not the popup", { id: EXTENSION_ID, url: `chrome-extension://${EXTENSION_ID}/other.html` }],
  ])("are refused from %s", async (_name, sender) => {
    const { api, background } = setup();
    expect(background.handle({ type: API, call: { op: "createMatch", jobId: 1, profileVersionId: 1 }, sessionTag: TAG }, sender)).toBeNull();
    expect(api.requests).toHaveLength(0);
  });

  it.each([
    [{ op: "requirements", jobId: "1/../../internal" }],
    [{ op: "requirements", jobId: 1, email: "someone@else.test" }],
    [{ op: "reprocess", jobId: 1 }],
    [{ op: "createMatch", jobId: 1, profileVersionId: 1, userId: 2 }],
  ])("refuse a call that is not one of the known shapes: %j", async (call) => {
    const { api, background } = setup();
    expect(background.handle({ type: API, call, sessionTag: TAG }, POPUP)).toBeNull();
    expect(api.requests).toHaveLength(0);
  });

  it("join an identical call already in flight rather than sending it twice", async () => {
    const { api, background } = setup();
    await background.handle({ type: SUBMIT, tabId: 1, payload: PAYLOAD, account: "dev@easyapply.local", sessionTag: TAG }, POPUP);
    const before = api.requests.length;
    const call = { type: API, call: { op: "createMatch", jobId: 1, profileVersionId: 1 }, sessionTag: TAG };
    const [first, second] = await Promise.all([background.handle(call, POPUP), background.handle(call, POPUP)]);
    expect(first).toEqual(second);
    expect(api.requests.length - before).toBe(1);
    // Afterwards it is a new request again (the server answers with the stored match).
    const third = await background.handle(call, POPUP);
    expect(third).toMatchObject({ ok: true, status: 200 });
    expect(api.requests.length - before).toBe(2);
  });

  it("never join a call in flight for one account from a popup showing another — even one with no ids", async () => {
    const { api, background } = setup();
    const forA = background.handle({ type: API, call: { op: "profileVersions" }, sessionTag: TAG }, POPUP);
    const forB = background.handle({ type: API, call: { op: "profileVersions" }, sessionTag: fakeSessionTag("b@example.test") }, POPUP);
    expect(forB).not.toBe(forA);
    const [a, b] = await Promise.all([forA, forB]);
    expect(api.requests.map((request) => request.headers["x-easyapply-session"])).toEqual([TAG, fakeSessionTag("b@example.test")]);
    expect(a).toMatchObject({ ok: true });
    // The browser is A's: B's popup is told the account changed, not handed A's versions.
    expect(b).toMatchObject({ ok: false, kind: "session_changed" });
  });

  it("never join calls for an account with no known tag, and send them without one", async () => {
    const { api, background } = setup();
    const message = { type: API, call: { op: "profileVersions" }, sessionTag: null };
    const first = background.handle(message, POPUP);
    const second = background.handle(message, POPUP);
    expect(second).not.toBe(first);
    await Promise.all([first, second]);
    expect(api.requests).toHaveLength(2);
    for (const request of api.requests) expect(request.headers).not.toHaveProperty("x-easyapply-session");
  });

  it("record the route, status, timing and ids — and no body, email or text", async () => {
    const { background, lines, diagnostics } = setup();
    await background.handle({ type: SUBMIT, tabId: 1, payload: PAYLOAD, account: "dev@easyapply.local", sessionTag: TAG }, POPUP);
    await background.handle({ type: API, call: { op: "requirements", jobId: 1 }, sessionTag: TAG }, POPUP);
    await background.handle({ type: API, call: { op: "createMatch", jobId: 1, profileVersionId: 1 }, sessionTag: TAG }, POPUP);
    await background.handle({ type: API, call: { op: "match", matchId: 424242 }, sessionTag: TAG }, POPUP);

    const entries = diagnostics.entries();
    expect(entries.map((entry) => [entry.route, entry.outcome, entry.status])).toEqual([
      ["POST /jobs", "created", 201],
      ["GET /jobs/:id/requirements", "ok", 200],
      ["POST /jobs/:id/matches", "ok", 201],
      ["GET /matches/:id", "not_found", 404],
    ]);
    expect(entries[2]?.ids).toEqual({ jobId: 1, profileVersionId: 1 });
    for (const line of lines) {
      expect(line).not.toMatch(/@/);
      expect(line).not.toContain("private description");
      expect(line).not.toContain("Senior Backend Engineer");
      expect(line).not.toContain("SQL");
    }
  });

  it("keep a bounded number of diagnostics", () => {
    const diagnostics = createDiagnostics({ log: () => undefined, limit: 3 });
    for (let index = 0; index < 5; index += 1) {
      diagnostics.record({ route: `GET /r${index}`, outcome: "ok", status: 200, ms: 1, ids: {} });
    }
    expect(diagnostics.entries().map((entry) => entry.route)).toEqual(["GET /r2", "GET /r3", "GET /r4"]);
  });
});
