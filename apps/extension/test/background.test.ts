import { apiRequest } from "../src/api/client";
import { submitJob, type SubmitOutcome } from "../src/api/jobs";
import { getSession } from "../src/api/session";
import { createDiagnostics } from "../src/background/diagnostics";
import { createBackground, submissionKey, type SessionStore } from "../src/background/service";
import type { JobIngestionRequest } from "../src/contract";
import { LAST_SUBMISSION, SESSION, SUBMIT } from "../src/shared/messages";
import { createFakeApi, fakeSessionTag } from "./support/fake-api";
import { EXTENSION_ID } from "./support/fake-chrome";
import { JOB_URL } from "./support/fixtures";

const PAYLOAD: JobIngestionRequest = {
  source_type: "greenhouse",
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: "Build things.",
};

const POPUP = { id: EXTENSION_ID, url: `chrome-extension://${EXTENSION_ID}/popup.html` };
/** The tag the fake web app gives the configured development account. */
const TAG = fakeSessionTag("dev@easyapply.local");

/** A save as the popup sends it. */
function saveMessage(sessionTag: unknown = TAG, tabId = 7): Record<string, unknown> {
  return { type: SUBMIT, tabId, payload: PAYLOAD, account: "dev@easyapply.local", sessionTag };
}

function memoryStore(): SessionStore & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    async get(key) {
      return data.get(key);
    },
    async set(key, value) {
      data.set(key, value);
    },
    async remove(key) {
      data.delete(key);
    },
  };
}

function setup() {
  const api = createFakeApi();
  const store = memoryStore();
  const background = createBackground({
    submit: (payload, sessionTag) => submitJob(payload, { webUrl: "http://localhost:3000", sessionTag, fetch: api.fetch }),
    session: () => getSession({ webUrl: "http://localhost:3000", fetch: api.fetch }),
    request: (prepared, sessionTag) => apiRequest(prepared, { webUrl: "http://localhost:3000", sessionTag, fetch: api.fetch }),
    store,
    extensionId: EXTENSION_ID,
    diagnostics: createDiagnostics({ log: () => undefined }),
  });
  return { api, store, background };
}

describe("who may ask for a save", () => {
  it("answers the popup", async () => {
    const { background, api } = setup();
    const outcome = await background.handle(saveMessage(), POPUP);
    expect((outcome as SubmitOutcome).kind).toBe("created");
    expect(api.requests).toHaveLength(1);
  });

  it.each([
    ["a content script, inside a job page", { id: EXTENSION_ID, url: JOB_URL }],
    ["another extension", { id: "abcdefghijklmnopabcdefghijklmnop", url: "chrome-extension://abcdefghijklmnopabcdefghijklmnop/popup.html" }],
    ["another page of this extension", { id: EXTENSION_ID, url: `chrome-extension://${EXTENSION_ID}/other.html` }],
    ["nobody in particular", {}],
  ])("ignores %s", (_who, sender) => {
    const { background, api } = setup();
    expect(background.handle(saveMessage(), sender)).toBeNull();
    expect(api.requests).toHaveLength(0);
  });

  it("ignores messages that are not its own", () => {
    const { background } = setup();
    expect(background.handle({ type: "easyapply/extract" }, POPUP)).toBeNull();
    expect(background.handle({ type: SUBMIT, payload: PAYLOAD }, POPUP)).toBeNull();
    expect(background.handle("hello", POPUP)).toBeNull();
  });

  it.each([
    ["missing", undefined],
    ["not a string", 42],
    ["empty", ""],
    ["too long", "a".repeat(129)],
    ["with characters a tag never has", "tag with spaces"],
    ["a header injection", "abc\r\nX-Dev-User-Email: someone@else.test"],
  ])("refuses a save whose session tag is %s", (_what, sessionTag) => {
    const { background, api } = setup();
    const message = { ...saveMessage(), sessionTag };
    if (sessionTag === undefined) delete message.sessionTag;
    expect(background.handle(message, POPUP)).toBeNull();
    expect(api.requests).toHaveLength(0);
  });
});

describe("the save carries the account's tag", () => {
  it("as X-EasyApply-Session, when the popup knows it", async () => {
    const { background, api } = setup();
    await background.handle(saveMessage(), POPUP);
    expect(api.saves[0]?.headers["x-easyapply-session"]).toBe(TAG);
  });

  it("not at all when the account's tag is unknown", async () => {
    const { background, api } = setup();
    await background.handle(saveMessage(null), POPUP);
    expect(api.saves[0]?.headers).not.toHaveProperty("x-easyapply-session");
  });
});

describe("one request per posting at a time", () => {
  it("joins a second click to the request already in flight", async () => {
    const { background, api } = setup();
    const first = background.handle(saveMessage(), POPUP);
    const second = background.handle(saveMessage(), POPUP);
    expect(second).toBe(first);
    await first;
    expect(api.requests).toHaveLength(1);
  });

  it("does not hand one account's save in flight to a popup showing another account", async () => {
    const { background } = setup();
    const first = background.handle(saveMessage(TAG), POPUP);
    const second = background.handle(saveMessage(fakeSessionTag("someone@else.test")), POPUP);
    expect(second).not.toBe(first);
    await Promise.all([first, second]);
  });

  it("joins nothing for an account with no known tag — unknown is nobody's", async () => {
    const { background, api } = setup();
    const first = background.handle(saveMessage(null), POPUP);
    const second = background.handle(saveMessage(null), POPUP);
    expect(second).not.toBe(first);
    await Promise.all([first, second]);
    // Two requests; the API's own rule makes the second "already in EasyApply".
    expect(api.saves).toHaveLength(2);
    expect(api.jobs).toHaveLength(1);
  });

  it("sends again once the first has finished — the API then says it already has it", async () => {
    const { background, api } = setup();
    await background.handle(saveMessage(), POPUP);
    const again = (await background.handle(saveMessage(), POPUP)) as SubmitOutcome;
    expect(again.kind).toBe("existing");
    expect(api.requests).toHaveLength(2);
    expect(api.jobs).toHaveLength(1);
  });
});

describe("remembering a save for the tab", () => {
  it("keeps what the popup shows and whose save it was, and not the description", async () => {
    const { background, store } = setup();
    await background.handle(saveMessage(), POPUP);
    expect(store.data.get(submissionKey(7))).toEqual({
      tabId: 7,
      account: "dev@easyapply.local",
      sessionTag: TAG,
      sourceUrl: JOB_URL,
      externalJobId: "4012345002",
      outcome: "created",
      jobId: 1,
      jobTitle: "Senior Backend Engineer",
      savedAt: "2026-09-26T09:00:00Z",
    });
    expect(await background.handle({ type: LAST_SUBMISSION, tabId: 7 }, POPUP)).toMatchObject({ jobId: 1, sessionTag: TAG });
  });

  it("answers a record kept without a well-formed tag as an unknown account's", async () => {
    const { background, store } = setup();
    store.data.set(submissionKey(7), { tabId: 7, account: "dev@easyapply.local", sourceUrl: JOB_URL, jobId: 1 });
    expect(await background.handle({ type: LAST_SUBMISSION, tabId: 7 }, POPUP)).toMatchObject({ jobId: 1, sessionTag: null });
    store.data.set(submissionKey(7), { tabId: 7, account: null, sessionTag: "not a tag", sourceUrl: JOB_URL, jobId: 1 });
    expect(await background.handle({ type: LAST_SUBMISSION, tabId: 7 }, POPUP)).toMatchObject({ sessionTag: null });
  });

  it("remembers nothing when the save failed", async () => {
    const { background, store, api } = setup();
    api.respondNextWith("network-error");
    await background.handle(saveMessage(), POPUP);
    expect(store.data.size).toBe(0);
    expect(await background.handle({ type: LAST_SUBMISSION, tabId: 7 }, POPUP)).toBeNull();
  });

  it("forgets a closed tab", async () => {
    const { background, store } = setup();
    await background.handle(saveMessage(), POPUP);
    await background.forgetTab(7);
    expect(store.data.size).toBe(0);
  });

  it("still reports a successful save when storage fails", async () => {
    const api = createFakeApi();
    const background = createBackground({
      submit: (payload, sessionTag) => submitJob(payload, { webUrl: "http://localhost:3000", sessionTag, fetch: api.fetch }),
      session: () => getSession({ webUrl: "http://localhost:3000", fetch: api.fetch }),
      request: (prepared, sessionTag) => apiRequest(prepared, { webUrl: "http://localhost:3000", sessionTag, fetch: api.fetch }),
      store: {
        get: () => Promise.reject(new Error("quota")),
        set: () => Promise.reject(new Error("quota")),
        remove: () => Promise.reject(new Error("quota")),
      },
      extensionId: EXTENSION_ID,
      diagnostics: createDiagnostics({ log: () => undefined }),
    });
    const outcome = (await background.handle(saveMessage(), POPUP)) as SubmitOutcome;
    expect(outcome.kind).toBe("created");
    expect(await background.handle({ type: LAST_SUBMISSION, tabId: 7 }, POPUP)).toBeNull();
  });
});

describe("the session question", () => {
  it("is answered for the popup, from the web app", async () => {
    const { background } = setup();
    expect(await background.handle({ type: SESSION }, POPUP)).toEqual({
      kind: "signed_in",
      mode: "development",
      label: "dev@easyapply.local",
      sessionTag: TAG,
    });
  });

  it("is not answered for a content script — a page does not get to learn who you are", () => {
    const { background, api } = setup();
    expect(background.handle({ type: SESSION }, { id: EXTENSION_ID, url: JOB_URL })).toBeNull();
    expect(api.requests).toHaveLength(0);
  });
});
