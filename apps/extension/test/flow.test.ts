/**
 * Capture → review → save, through the extension's real entry points.
 *
 * The service worker (src/background/index.ts) and the popup
 * (src/popup/index.ts) are the shipped modules, imported as Chrome would load
 * them. What is fake is the edge: the Chrome APIs (test/support/fake-chrome.ts)
 * and the EasyApply web app answering at http://localhost:3000
 * (test/support/fake-api.ts, which deduplicates the way the API does). The
 * browser suite in e2e/ runs the same journey in real Chromium.
 */

import type { JobIngestionRequest } from "../src/contract";
// The module graph the entry points load, imported statically so it is
// transformed while this file is collected — not inside the first test's time
// budget. (Each test still loads the entry points fresh with vi.resetModules.)
import "../src/popup/app";
import "../src/background/service";
import { createFakeApi, type FakeApi, type FakeSession, CONFIGURED } from "./support/fake-api";
import { createFakeChrome, EXTENSION_ID, type FakeTab } from "./support/fake-chrome";
import { BOARD_URL, EXPECTED_DESCRIPTION, JOB_URL, boardDocument, jobDocument } from "./support/fixtures";

const EXPECTED_PAYLOAD: JobIngestionRequest = {
  source_type: "greenhouse",
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: EXPECTED_DESCRIPTION,
};

let api: FakeApi;
let fake: ReturnType<typeof createFakeChrome>;
let activeTab: { id: number };

async function launch(tabs: FakeTab[], { session = CONFIGURED }: { session?: FakeSession } = {}): Promise<void> {
  vi.resetModules();
  fake = createFakeChrome(tabs, { activeTabId: tabs[0]?.id });
  activeTab = { id: tabs[0]?.id ?? 0 };
  // The active tab can change between popups; the fake reads it from here.
  const query = fake.chrome.tabs.query;
  fake.chrome.tabs.query = async (options) =>
    (await query({})).filter((tab) => !options.active || tab.id === activeTab.id);
  api = createFakeApi({ session });
  vi.stubGlobal("chrome", fake.chrome);
  vi.stubGlobal("fetch", api.fetch);
  await import("../src/background/index");
}

/** Open the popup, as clicking the toolbar button does, and wait for it to settle. */
async function openPopup(): Promise<HTMLElement> {
  const root = document.createElement("main");
  root.id = "app";
  document.body.replaceChildren(root);
  vi.resetModules();
  await import("../src/popup/index");
  await vi.waitFor(() => {
    if (!root.querySelector("#review, #blocked")) throw new Error("still reading the page");
  });
  return root;
}

function field(root: HTMLElement, name: string): HTMLInputElement | HTMLTextAreaElement {
  const control = root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#field-${name}`);
  if (!control) throw new Error(`no field ${name}`);
  return control;
}

function edit(root: HTMLElement, name: string, value: string): void {
  const control = field(root, name);
  control.value = value;
  control.dispatchEvent(new Event("input", { bubbles: true }));
}

function exactRequest(root: HTMLElement): JobIngestionRequest {
  return JSON.parse(root.querySelector("#exact-body")?.textContent ?? "null");
}

function saveButton(root: HTMLElement): HTMLButtonElement {
  const button = root.querySelector<HTMLButtonElement>("#save");
  if (!button) throw new Error("no save button");
  return button;
}

async function waitForOutcome(root: HTMLElement): Promise<HTMLElement> {
  let notice: HTMLElement | null = null;
  await vi.waitFor(() => {
    notice = root.querySelector<HTMLElement>("#outcome-notice");
    if (!notice) throw new Error("no outcome yet");
  });
  return notice as unknown as HTMLElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("capture, review, save", () => {
  it("shows exactly what will be saved, sends it only when asked, and reports the new job", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    const popup = await openPopup();

    // Review: every field as read, and the exact request.
    expect(field(popup, "job_title").value).toBe("Senior Backend Engineer");
    expect(field(popup, "company_name").value).toBe("Acme Robotics");
    expect(field(popup, "location_raw").value).toBe("Remote, Berlin");
    expect(field(popup, "description_text").value).toBe(EXPECTED_DESCRIPTION);
    expect(popup.querySelector("#identity-source")?.textContent).toBe("greenhouse");
    expect(popup.querySelector("#identity-external-id")?.textContent).toBe("4012345002");
    expect(popup.querySelector("#identity-url")?.textContent).toBe(JOB_URL);
    expect(exactRequest(popup)).toEqual(EXPECTED_PAYLOAD);
    expect(saveButton(popup).disabled).toBe(false);

    // Nothing has been sent by opening it.
    expect(api.saves).toHaveLength(0);

    // An edit changes the exact request before anything is sent.
    edit(popup, "job_title", "Senior Backend Engineer (Platform)");
    expect(exactRequest(popup).job_title).toBe("Senior Backend Engineer (Platform)");

    saveButton(popup).click();
    const notice = await waitForOutcome(popup);

    expect(notice.textContent).toContain("Saved to EasyApply.");
    expect(notice.textContent).toContain("EasyApply has read its requirements.");
    expect(popup.querySelector<HTMLAnchorElement>("#open-job")?.href).toBe("http://localhost:3000/jobs/1");

    // What went on the wire is what was on screen — and nothing else.
    expect(api.saves).toHaveLength(1);
    const [request] = api.saves;
    expect(request?.url).toBe("http://localhost:3000/api/backend/jobs");
    expect(request?.method).toBe("POST");
    expect(JSON.parse(request?.body ?? "")).toEqual(exactRequest(popup));
    expect(JSON.parse(request?.body ?? "")).toEqual({ ...EXPECTED_PAYLOAD, job_title: "Senior Backend Engineer (Platform)" });
    // No identity of its own — only the tag of the account the web app named, handed back.
    expect(Object.keys(request?.headers ?? {}).sort()).toEqual(["accept", "content-type", "x-easyapply-session"]);
    expect(request?.headers["x-easyapply-session"]).toBe(api.currentTag);

    // The form is now a record of what was sent.
    expect(saveButton(popup).disabled).toBe(true);
    expect(saveButton(popup).textContent).toBe("Saved");
    expect(field(popup, "job_title").readOnly).toBe(true);
  });

  it("reopened on the same posting, says it is saved and offers no second save", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    saveButton(await openPopup()).click();
    await waitForOutcome(document.getElementById("app")!);

    const again = await openPopup();
    const notice = await waitForOutcome(again);
    expect(notice.textContent).toContain("Saved to EasyApply.");
    expect(notice.textContent).toContain("from this tab");
    expect(saveButton(again).disabled).toBe(true);
    expect(api.saves).toHaveLength(1);
  });

  it("a double click sends one request", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    const popup = await openPopup();
    saveButton(popup).click();
    saveButton(popup).click();
    await waitForOutcome(popup);
    expect(api.saves).toHaveLength(1);
  });

  it("the same posting from another tab is 'already in EasyApply' — one job, not two", async () => {
    await launch([
      { id: 11, url: JOB_URL, document: jobDocument() },
      { id: 12, url: `${JOB_URL}?gh_src=newsletter`, document: jobDocument() },
    ]);
    saveButton(await openPopup()).click();
    await waitForOutcome(document.getElementById("app")!);

    activeTab = { id: 12 };
    const second = await openPopup();
    expect(saveButton(second).disabled).toBe(false);
    edit(second, "job_title", "Edited after saving");
    saveButton(second).click();
    const notice = await waitForOutcome(second);

    expect(notice.textContent).toContain("Already in EasyApply.");
    expect(notice.textContent).toContain("nothing was added or changed");
    expect(notice.textContent).toContain("differs from what you reviewed in: title");
    expect(second.querySelector<HTMLAnchorElement>("#open-job")?.href).toBe("http://localhost:3000/jobs/1");
    expect(api.jobs).toHaveLength(1);
  });
});

describe("when a save fails", () => {
  it("says the API is down, and saves on the retry", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    api.respondNextWith({ status: 502, body: JSON.stringify({ detail: "The EasyApply API is unreachable.", code: "api_unreachable" }) });
    const popup = await openPopup();

    saveButton(popup).click();
    const failure = await waitForOutcome(popup);
    expect(failure.textContent).toContain("The EasyApply API isn't running.");
    expect(saveButton(popup).disabled).toBe(false);
    expect(saveButton(popup).textContent).toBe("Try again");
    expect(fake.session.size).toBe(0);

    saveButton(popup).click();
    await vi.waitFor(() => {
      expect(popup.querySelector("#outcome-notice")?.textContent).toContain("Saved to EasyApply.");
    });
    expect(api.jobs).toHaveLength(1);
  });

  it("tells the person the exact line when the web app has not allowed this extension", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    api.respondNextWith({ status: 403, body: JSON.stringify({ detail: "Cross-site requests are not accepted.", code: "cross_site_request" }) });
    const popup = await openPopup();
    saveButton(popup).click();
    const notice = await waitForOutcome(popup);
    expect(notice.textContent).toContain(`PROXY_ALLOWED_EXTENSION_IDS=${EXTENSION_ID}`);
  });

  it("will not send a job without a title", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    const popup = await openPopup();
    edit(popup, "job_title", "   ");
    expect(saveButton(popup).disabled).toBe(true);
    expect(popup.querySelector("#issues")?.textContent).toContain("A job title is required.");
    popup.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(api.saves).toHaveLength(0);
  });
});

describe("pages it cannot save", () => {
  it("a site with no adapter: says what is supported, sends nothing", async () => {
    await launch([{ id: 5, url: "https://www.linkedin.com/jobs/view/123" }]);
    const popup = await openPopup();
    expect(popup.querySelector("#blocked")?.textContent).toContain("Not a supported job page.");
    expect(popup.querySelector("#blocked")?.textContent).toContain("Greenhouse");
    expect(popup.querySelector("#save")).toBeNull();
    expect(api.saves).toHaveLength(0);
  });

  it("a Greenhouse tab with no content script yet: says to wait or reload, and offers a retry", async () => {
    const tab: FakeTab = { id: 5, url: JOB_URL };
    await launch([tab]);
    const popup = await openPopup();
    const blocked = popup.querySelector("#blocked")?.textContent ?? "";
    expect(blocked).toContain("Can't read this page yet.");
    expect(blocked).toContain("still loading");
    expect(blocked).toContain("reload the page");

    // The page finishes loading and its content script starts.
    tab.document = jobDocument();
    popup.querySelector<HTMLButtonElement>("button.secondary")?.click();
    await vi.waitFor(() => {
      expect(popup.querySelector("#review")).not.toBeNull();
    });
  });

  it("a board rather than a posting", async () => {
    await launch([{ id: 5, url: BOARD_URL, document: boardDocument() }]);
    const popup = await openPopup();
    expect(popup.querySelector("#blocked")?.textContent).toContain("No single job here.");
  });

  it("a posting whose layout is not recognised: an explicit failure, nothing sent, and a retry", async () => {
    const broken = jobDocument();
    broken.querySelector("main")?.replaceChildren();
    const tab: FakeTab = { id: 5, url: JOB_URL, document: broken };
    await launch([tab]);
    const popup = await openPopup();

    const blocked = popup.querySelector("#blocked")?.textContent ?? "";
    expect(blocked).toContain("Couldn't read this job.");
    expect(blocked).toContain("job title and job description");
    expect(blocked).toContain("Nothing has been saved.");
    expect(api.saves).toHaveLength(0);

    // The page finished loading; trying again reads it.
    tab.document = jobDocument();
    popup.querySelector<HTMLButtonElement>("button.secondary")?.click();
    await vi.waitFor(() => {
      expect(popup.querySelector("#review")).not.toBeNull();
    });
  });
});

describe("posting text is data, never markup", () => {
  it("shows a title full of HTML as text, and runs none of it", async () => {
    const hostile = jobDocument();
    const heading = hostile.querySelector(".job__title h1");
    if (heading) heading.textContent = `<img src=x onerror="window.pwned=true"> Engineer`;
    hostile.title = "Job Application for Engineer at <b>Acme</b>";
    await launch([{ id: 11, url: JOB_URL, document: hostile }]);
    const popup = await openPopup();

    expect(field(popup, "job_title").value).toBe(`<img src=x onerror="window.pwned=true"> Engineer`);
    expect(popup.querySelector("img, b")).toBeNull();
    expect((window as unknown as { pwned?: boolean }).pwned).toBeUndefined();
  });
});

describe("which account the job goes to", () => {
  const OWNER: FakeSession = { mode: "dev-session", email: "owner@easyapply.test" };

  it("development: names the configured account before anything is sent", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    const popup = await openPopup();
    expect(popup.querySelector("#account-email")?.textContent).toBe(
      "Saving as dev@easyapply.local — the account this EasyApply web app is configured as.",
    );
    expect(saveButton(popup).disabled).toBe(false);
  });

  it("dev-session: names the signed-in account, and saves into it", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: OWNER });
    const popup = await openPopup();
    expect(popup.querySelector("#account-email")?.textContent).toBe("Saving as owner@easyapply.test.");

    saveButton(popup).click();
    expect((await waitForOutcome(popup)).textContent).toContain("Saved to EasyApply.");
    expect(api.jobs.map((job) => job.owner)).toEqual(["owner@easyapply.test"]);
    expect(api.saves[0]?.credentials).toBe("include");
  });

  it("signed out: shows the review, offers sign-in, and sends nothing", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: null });
    const popup = await openPopup();

    // The review is still there to read: signing in is the only thing missing.
    expect(field(popup, "job_title").value).toBe("Senior Backend Engineer");
    const notice = popup.querySelector("#account-notice")?.textContent ?? "";
    expect(notice).toContain("Sign in to EasyApply to save this job.");
    expect(popup.querySelector<HTMLAnchorElement>("#sign-in")?.href).toBe("http://localhost:3000/");
    expect(saveButton(popup).disabled).toBe(true);

    popup.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(api.saves).toHaveLength(0);
  });

  it("a session that ends before Save: says it expired and nothing was saved, and stops offering the button", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: OWNER });
    const popup = await openPopup();
    expect(saveButton(popup).disabled).toBe(false);

    // Signed out in another tab while the popup was open.
    api.setSession(null);
    saveButton(popup).click();
    const notice = await waitForOutcome(popup);

    expect(notice.textContent).toContain("Your EasyApply session expired. Sign in again.");
    expect(notice.textContent).toContain("EasyApply couldn't save this job.");
    expect(popup.querySelector<HTMLAnchorElement>("#session-sign-in")?.href).toBe("http://localhost:3000/");
    expect(popup.querySelector("#account-notice")?.textContent).toContain("Sign in to EasyApply to save this job.");
    expect(saveButton(popup).disabled).toBe(true);
    expect(api.jobs).toHaveLength(0);
    const ids = [...popup.querySelectorAll("[id]")].map((node) => node.id);
    expect(ids.length).toBe(new Set(ids).size);
  });

  it("signed out from the start is not an expiry: it asks to sign in", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: null });
    const popup = await openPopup();
    expect(popup.querySelector("#account-notice")?.textContent).toContain("Sign in to EasyApply to save this job.");
    expect(popup.textContent).not.toContain("expired");
  });

  it("a different account signed in before Save: nothing is saved, nothing of the first is left, Reload continues as the second", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: OWNER });
    const popup = await openPopup();
    expect(popup.querySelector("#account-email")?.textContent).toBe("Saving as owner@easyapply.test.");
    edit(popup, "job_title", "Edited by the owner");

    // Someone else signs in, in another tab, while the popup is open.
    api.setSession({ mode: "dev-session", email: "second@easyapply.test" });
    saveButton(popup).click();
    await vi.waitFor(() => {
      expect(popup.querySelector("#session-changed")?.textContent).toContain(
        "You're signed in to EasyApply as a different account now.",
      );
    });

    // The save was refused by the proxy (it carried the owner's tag), and nothing was saved for anyone.
    expect(api.saves).toHaveLength(1);
    expect(api.saves[0]?.headers["x-easyapply-session"]).not.toBe(api.currentTag);
    expect(api.jobs).toHaveLength(0);
    expect(fake.session.size).toBe(0);
    // Nothing of the owner's view is left: not the account, not the reviewed draft, not a button to send it.
    expect(popup.textContent).not.toContain("owner@easyapply.test");
    expect(popup.querySelector("#review, #save, #job-panel, #application-panel")).toBeNull();

    popup.querySelector<HTMLButtonElement>("#session-reload")?.click();
    await vi.waitFor(() => {
      expect(popup.querySelector("#account-email")?.textContent).toBe("Saving as second@easyapply.test.");
    });
    // The page as it reads now, not the owner's edit.
    expect(field(popup, "job_title").value).toBe("Senior Backend Engineer");
    saveButton(popup).click();
    expect((await waitForOutcome(popup)).textContent).toContain("Saved to EasyApply.");
    expect(api.jobs.map((job) => job.owner)).toEqual(["second@easyapply.test"]);
  });

  it("two people in one browser, one after the other, save into their own accounts", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: OWNER });
    saveButton(await openPopup()).click();
    await waitForOutcome(document.getElementById("app")!);

    // Someone else signs in; the same posting is theirs to save, not "already saved".
    api.setSession({ mode: "dev-session", email: "second@easyapply.test" });
    const popup = await openPopup();
    // The worker remembers the first person's save from this tab; it is not the second person's.
    expect(fake.session.size).toBe(1);
    expect(popup.querySelector("#outcome-notice")).toBeNull();
    expect(popup.querySelector("#account-email")?.textContent).toBe("Saving as second@easyapply.test.");
    saveButton(popup).click();
    expect((await waitForOutcome(popup)).textContent).toContain("Saved to EasyApply.");
    expect(api.jobs.map((job) => job.owner)).toEqual(["owner@easyapply.test", "second@easyapply.test"]);
  });

  it("two accounts that share an email are two accounts: the second is not told the first one's job is saved", async () => {
    const first: FakeSession = { mode: "entra", email: "shared@example.test", account: "tenant-1:oid-1" };
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: first });
    saveButton(await openPopup()).click();
    await waitForOutcome(document.getElementById("app")!);

    api.setSession({ mode: "entra", email: "shared@example.test", account: "tenant-1:oid-2" });
    const popup = await openPopup();
    expect(popup.querySelector("#account-email")?.textContent).toBe("Saving as shared@example.test.");
    expect(popup.querySelector("#outcome-notice")).toBeNull();
    expect(saveButton(popup).disabled).toBe(false);
  });

  it("the same account signed in again is still the same account: its save is remembered", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], { session: OWNER });
    saveButton(await openPopup()).click();
    await waitForOutcome(document.getElementById("app")!);

    api.setSession(null);
    api.setSession(OWNER);
    const popup = await openPopup();
    expect((await waitForOutcome(popup)).textContent).toContain("from this tab");
    expect(saveButton(popup).disabled).toBe(true);
  });

  it.each([
    ["an email", { email: "ada@example.test", name: "Ada Lovelace" }, "Saving as ada@example.test."],
    ["only a name", { email: null, name: "Ada Lovelace" }, "Saving as Ada Lovelace."],
    ["neither", { email: null, name: null }, "Saving as your EasyApply account."],
  ])("entra with %s: signed in, named as well as it can be, and saves", async (_what, identity, shown) => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], {
      session: { mode: "entra", account: "tenant:oid-ada", ...identity },
    });
    const popup = await openPopup();
    expect(popup.querySelector("#account-email")?.textContent).toBe(shown);
    expect(saveButton(popup).disabled).toBe(false);
    saveButton(popup).click();
    expect((await waitForOutcome(popup)).textContent).toContain("Saved to EasyApply.");
    expect(api.jobs.map((job) => job.owner)).toEqual(["tenant:oid-ada"]);
  });

  it("a web app that gives no account tag: saves without one, and never takes a remembered save as this account's", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }], {
      session: { mode: "dev-session", email: "owner@easyapply.test", sessionTag: null },
    });
    const popup = await openPopup();
    saveButton(popup).click();
    expect((await waitForOutcome(popup)).textContent).toContain("Saved to EasyApply.");
    expect(api.saves[0]?.headers).not.toHaveProperty("x-easyapply-session");
    // Whose view the panel would be is unknown, so it loads nothing.
    expect(popup.querySelector("#panel-unavailable")).not.toBeNull();
    expect(api.backend).toHaveLength(1);

    const again = await openPopup();
    expect(again.querySelector("#outcome-notice")).toBeNull();
    expect(saveButton(again).disabled).toBe(false);
  });

  it("unknown: warns, and still lets the save try — it will say what happened", async () => {
    await launch([{ id: 11, url: JOB_URL, document: jobDocument() }]);
    api.respondNextWith("network-error", "/api/auth/session");
    const popup = await openPopup();
    expect(popup.querySelector("#account-notice")?.textContent).toContain(
      "Couldn't check which account this would be saved to.",
    );
    expect(saveButton(popup).disabled).toBe(false);
  });
});
