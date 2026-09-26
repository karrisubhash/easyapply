/**
 * The application form, through the real popup and content-script handler:
 * nothing is read from the profile until the person asks, every proposal is
 * shown, only ticked values reach the page, and nothing is submitted.
 */

import { FILL_FORM } from "../src/shared/messages";
import { SNAPSHOT } from "./support/backend-fixtures";
import type { FakeTab } from "./support/fake-chrome";
import { JOB_URL, fixtureHtml, parseDocument } from "./support/fixtures";
import { byId, launch, openPopup, save, text, waitForText } from "./support/harness";

const ME = "dev@easyapply.local";

function applicationTab(): FakeTab {
  return { id: 21, url: JOB_URL, document: parseDocument(fixtureHtml("greenhouse-application.html")) };
}

async function withForm(session?: NonNullable<Parameters<typeof launch>[0]>["session"]) {
  const tab = applicationTab();
  const harness = await launch({ tabs: [tab], session });
  const sent: unknown[] = [];
  const original = harness.fake.chrome.tabs.sendMessage;
  harness.fake.chrome.tabs.sendMessage = async (tabId: number, message: unknown) => {
    sent.push(message);
    return original(tabId, message);
  };
  const form = tab.document!.getElementById("application-form") as HTMLFormElement;
  const submitted: string[] = [];
  form.addEventListener("submit", () => submitted.push("submit"));
  const root = await openPopup();
  await waitForText(root, "application-status", /application form/);
  return { ...harness, tab, root, sent, submitted, page: tab.document! };
}

beforeEach(() => {
  vi.spyOn(console, "info").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("an application form on the page", () => {
  it("is found and described, and nothing is read from the profile until the person asks", async () => {
    const { root, api } = await withForm();
    expect(text(root, "application-status")).toBe("EasyApply found this page's Greenhouse application form: 28 fields.");
    expect(root.querySelector("#application-prepare")).not.toBeNull();
    expect(api.requests.some((request) => request.url.includes("/profile/versions/current"))).toBe(false);
    expect(text(root, "application")).toContain("never submits the application");
  });

  it("proposes values with their confidence, and marks every question it will not answer", async () => {
    const { root, api } = await withForm();
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "application-status", /Proposed from your profile version 1/);
    expect(api.requests.filter((request) => request.url.endsWith("/api/backend/profile/versions/current"))).toHaveLength(1);

    const ready = [...root.querySelectorAll("#application-ready .proposal")].map((row) => [
      row.querySelector(".proposal__label")?.textContent,
      row.querySelector(".proposal__value")?.textContent,
      row.getAttribute("data-confidence"),
      (row.querySelector("input[type=checkbox]") as HTMLInputElement).checked,
    ]);
    expect(ready).toEqual([
      ["First Name", "Ada", "derived", true],
      ["Last Name", "Lovelace", "derived", true],
      ["Email", "ada@example.test", "exact", true],
      ["Phone", "+44 20 7946 0000", "exact", true],
      ["LinkedIn Profile", "https://www.linkedin.com/in/ada-lovelace", "exact", true],
      ["Website", "https://ada.example.test", "exact", true],
      ["GitHub URL", "https://github.com/ada", "exact", true],
    ]);
    expect(text(root, "application-ready")).toContain("Adapted from your profile — check it");

    const sensitive = [...root.querySelectorAll("#application-review [data-sensitive]")].map((row) => row.getAttribute("data-sensitive"));
    expect(sensitive).toEqual([
      "work_authorization",
      "sponsorship",
      "location_eligibility",
      "relocation",
      "salary",
      "years_of_experience",
      "notice_period",
      "disability",
      "employment_history",
      "demographic",
      "demographic",
      "demographic",
      "demographic",
    ]);
    expect(text(root, "application-review")).toContain("Review required");
    expect(text(root, "application-review")).toContain("Your profile says: London, United Kingdom");
    expect(text(root, "application-attach")).toContain("Attach your resume yourself");
    expect(byId<HTMLButtonElement>(root, "application-fill").textContent).toBe("Fill 7 selected fields");
  });

  it("fills only the fields the person left ticked, and never submits", async () => {
    const { root, sent, submitted, page } = await withForm();
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "application-status", /Proposed from/);

    const phone = root.querySelector<HTMLInputElement>('#application-ready input[data-key="id:phone"]')!;
    phone.checked = false;
    phone.dispatchEvent(new Event("change", { bubbles: true }));
    expect(byId<HTMLButtonElement>(root, "application-fill").textContent).toBe("Fill 6 selected fields");

    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "application-report", /Filled 6 fields on the page/);

    const fills = sent.filter((message) => (message as { type?: string }).type === FILL_FORM) as { instructions: { key: string; value: string }[] }[];
    expect(fills).toHaveLength(1);
    expect(fills[0]!.instructions.map((instruction) => [instruction.key, instruction.value])).toEqual([
      ["id:first_name", "Ada"],
      ["id:last_name", "Lovelace"],
      ["id:email", "ada@example.test"],
      ["id:question_61000001", "https://www.linkedin.com/in/ada-lovelace"],
      ["id:question_61000002", "https://ada.example.test"],
      ["id:question_61000003", "https://github.com/ada"],
    ]);
    // Nothing else from the profile went to the page.
    const wire = JSON.stringify(sent);
    for (const unsent of ["+44 20 7946 0000", "London", "Python", "Analytical Engines", SNAPSHOT.summary!]) expect(wire).not.toContain(unsent);

    const value = (id: string) => (page.getElementById(id) as HTMLInputElement).value;
    expect(value("first_name")).toBe("Ada");
    expect(value("email")).toBe("ada@example.test");
    expect(value("phone")).toBe("");
    expect(value("question_61000010")).toBe("");
    expect(submitted).toEqual([]);

    const report = text(root, "application-report");
    expect(report).toContain("submit it yourself when you're ready");
    expect(report).toContain("EasyApply hasn't submitted anything, and won't.");
    expect(report).toContain("required questions marked for you");
    expect(report).not.toMatch(/\bsubmitted\b(?! anything)/);
  });

  it("reports what it left alone when the page changed in the meantime", async () => {
    const { root, page } = await withForm();
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "application-status", /Proposed from/);
    (page.getElementById("email") as HTMLInputElement).value = "typed-meanwhile@example.test";
    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "application-report", /Filled 6 fields/);
    expect(text(root, "application-skipped")).toBe("Email already had something in it, so it was left as it was.");
    expect((page.getElementById("email") as HTMLInputElement).value).toBe("typed-meanwhile@example.test");
  });

  it("when who is signed in cannot be checked, says that — not 'sign in' — and reads nothing", async () => {
    const { api } = await withForm({ mode: "dev-session", email: ME });
    // The web app answers something that is not a session (as when it is down behind a proxy).
    api.setSession({ mode: "not-a-mode" as never, email: ME });
    const reopened = await openPopup();
    await waitForText(reopened, "application-session-unknown", /EasyApply couldn't check who is signed in\./);
    expect(reopened.querySelector("#application-signin")).toBeNull();
    expect(reopened.querySelector("#application-prepare")).toBeNull();
    expect(api.requests.some((request) => request.url.includes("/profile/"))).toBe(false);
  });

  it("signed out, offers nothing and reads nothing", async () => {
    const { api } = await withForm({ mode: "dev-session", email: ME });
    // The browser signs out of the web app; the popup is opened again.
    api.setSession(null);
    const reopened = await openPopup();
    await waitForText(reopened, "application-signin", /Sign in to EasyApply to prepare answers/);
    expect(reopened.querySelector("#application-prepare")).toBeNull();
    expect(api.requests.some((request) => request.url.includes("/profile/"))).toBe(false);
  });

  it("without a confirmed, current profile version, says so and proposes nothing", async () => {
    const { root, api } = await withForm();
    api.setProfile(ME, { versions: [api.version(ME, 1, { status: "draft", is_current: false })], snapshot: SNAPSHOT });
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "application-error", /don't have a confirmed, current profile version/);
    expect(root.querySelector("#application-fill")).toBeNull();
  });

  it("links the tailored resume for this job beside the resume upload, once it is ready", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      const { root, api } = await withForm();
      await save(root);
      await waitForText(root, "match-status", /No match/);
      byId<HTMLButtonElement>(root, "match-run").click();
      await waitForText(root, "match-status", /Matched against/);
      byId<HTMLButtonElement>(root, "tailor-run").click();
      await waitForText(root, "tailoring-status", /ready/);

      byId<HTMLButtonElement>(root, "application-prepare").click();
      await waitForText(root, "application-status", /Proposed from/);
      expect(byId<HTMLAnchorElement>(root, "application-tailored-download").href).toBe(
        `http://localhost:3000/api/backend/tailored-resumes/${api.runs[0]!.id}/artifact`,
      );
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the account behind the proposals", () => {
  const A = { mode: "dev-session" as const, email: "ada@example.test" };
  const B = { mode: "dev-session" as const, email: "grace@example.test" };

  async function prepared() {
    const harness = await withForm(A);
    byId<HTMLButtonElement>(harness.root, "application-prepare").click();
    await waitForText(harness.root, "application-status", /Proposed from your profile version 1/);
    expect(text(harness.root, "application-ready")).toContain("Lovelace");
    const read = harness.api.requests.find((request) => request.url.endsWith("/api/backend/profile/versions/current"));
    expect(read?.headers["x-easyapply-session"]).toBe(harness.api.currentTag);
    return harness;
  }

  const fills = (sent: unknown[]) => sent.filter((message) => (message as { type?: string }).type === FILL_FORM);

  it("another account signed in before Fill: nothing is filled, and nothing of the first account is left on screen", async () => {
    const { root, api, sent, page, submitted } = await prepared();
    api.setSession(B);
    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "session-changed", /^You're signed in to EasyApply as a different account now\./);

    expect(fills(sent)).toEqual([]);
    for (const id of ["first_name", "last_name", "email", "phone"]) expect((page.getElementById(id) as HTMLInputElement).value).toBe("");
    for (const unseen of ["Lovelace", "ada@example.test", "+44 20 7946 0000"]) expect(root.textContent).not.toContain(unseen);
    expect(root.querySelector("#application, #application-fill, #review")).toBeNull();
    expect(submitted).toEqual([]);

    // Reloaded, it is the second account's form to prepare, from the second account's profile.
    byId<HTMLButtonElement>(root, "session-reload").click();
    await waitForText(root, "account-email", /^Saving as grace@example\.test\.$/);
    await waitForText(root, "application-status", /application form/);
    expect(root.querySelector("#application-ready")).toBeNull();
  });

  it("another account signed in before Prepare: the profile read is refused, and nothing is proposed", async () => {
    const { root, api } = await withForm(A);
    api.setSession(B);
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "session-changed", /different account now/);
    expect(root.querySelector("#application-ready")).toBeNull();
    const read = api.requests.find((request) => request.url.endsWith("/api/backend/profile/versions/current"));
    expect(read?.headers["x-easyapply-session"]).not.toBe(api.currentTag);
  });

  it("signed out before Fill: the session expired, nothing is filled, and the proposals are gone", async () => {
    const { root, api, sent } = await prepared();
    api.setSession(null);
    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "application-error", /^Your EasyApply session expired\. Sign in again\./);
    expect(text(root, "application-error")).toContain("EasyApply couldn't fill the form.");
    expect(fills(sent)).toEqual([]);
    expect(root.querySelector("#application-ready")).toBeNull();
    expect(text(root, "account-notice")).toContain("Sign in to EasyApply to save this job.");
    const ids = [...root.querySelectorAll("[id]")].map((node) => node.id);
    expect(ids.length).toBe(new Set(ids).size);
  });

  it("the web app unreachable before Fill: it cannot tell whose session this is, so nothing is filled", async () => {
    const { root, api, sent } = await prepared();
    api.respondNextWith("network-error", "/api/auth/session");
    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "application-error", /EasyApply isn't reachable/);
    expect(text(root, "application-error")).toContain("EasyApply couldn't fill the form");
    expect(fills(sent)).toEqual([]);
  });

  it("a web app that gives no account tag: nothing is read from a profile, nothing is put on the page", async () => {
    const { root, api } = await withForm({ ...A, sessionTag: null });
    await waitForText(root, "application-unverified", /Couldn't check which account this browser is signed in as/);
    expect(root.querySelector("#application-prepare")).toBeNull();
    expect(api.requests.some((request) => request.url.includes("/profile/"))).toBe(false);
  });
});

describe("a Lever application page (the form on a page of its own)", () => {
  const LEVER_APPLY = "https://jobs.lever.co/acmerobotics/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f/apply";

  it("says there is nothing to save here, links the posting, and still prepares the form", async () => {
    const tab: FakeTab = { id: 31, url: LEVER_APPLY, document: parseDocument(fixtureHtml("lever-apply.html")) };
    const { api } = await launch({ tabs: [tab] });
    const root = await openPopup();
    expect(text(root, "blocked")).toContain("This is a job's application form.");
    expect(byId<HTMLAnchorElement>(root, "open-posting").href).toBe(LEVER_APPLY.replace(/\/apply$/, ""));
    expect(root.querySelector("#save")).toBeNull();

    await waitForText(root, "application-status", /Lever application form: 17 fields/);
    byId<HTMLButtonElement>(root, "application-prepare").click();
    await waitForText(root, "application-status", /Proposed from your profile version 1/);
    expect(text(root, "application-review")).toContain("work authorization");
    byId<HTMLButtonElement>(root, "application-fill").click();
    await waitForText(root, "application-report", /Filled 7 fields on the page/);
    const page = tab.document!;
    expect((page.querySelector('[name="name"]') as HTMLInputElement).value).toBe("Ada Lovelace");
    expect((page.querySelector('[name="location"]') as HTMLInputElement).value).toBe("London, United Kingdom");
    expect([...page.querySelectorAll<HTMLInputElement>("input[type=radio], input[type=checkbox]")].some((box) => box.checked)).toBe(false);
    // Nothing was saved from an application page.
    expect(api.saves).toHaveLength(0);
  });
});
