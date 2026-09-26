/**
 * Capture → review → save, in real Chromium with the built extension.
 *
 * What only a real browser can show: that Chrome accepts the manifest, injects
 * the content script on Greenhouse and nowhere else, carries messages between
 * page, popup and service worker, and lets the service worker reach the web
 * app — carrying the web app's own session cookie, which is the one browser
 * behaviour the authentication design rests on (docs/authentication.md). The
 * stub (default run) records the wire; against a real stack
 * (EXTENSION_E2E_WEB_URL, a web app in AUTH_MODE=dev-session) the proof is that
 * the web app then shows the job to the account that was signed in.
 */

import { EXTENSION_ID, WEB_URL } from "./env";
import { contentScriptAnswers, expect, openPopup, openPosting, signIn, signOut, test } from "./support";

/** Unique per run, so a real database that already holds a previous run's jobs cannot answer 200 by accident. */
const RUN = String(Date.now()).slice(-8);
const OWNER = `owner-${RUN}@ext-e2e.easyapply.test`;
const jobUrl = (n: number, board = "acmerobotics") => `https://job-boards.greenhouse.io/${board}/jobs/${RUN}${n}`;

test.describe("a Greenhouse posting", () => {
  test("is read, shown for review, and saved exactly as shown — once", async ({ context, worker, stub, jobSiteRequests }) => {
    await signIn(context, OWNER);
    const href = jobUrl(1);
    const { page, tabId } = await openPosting(context, worker, href);
    const pageBefore = await page.evaluate(() => document.documentElement.outerHTML);

    const popup = await openPopup(context, tabId);
    await expect(popup.locator("#field-job_title")).toHaveValue("Senior Backend Engineer");
    await expect(popup.locator("#field-company_name")).toHaveValue("Acme Robotics");
    await expect(popup.locator("#field-location_raw")).toHaveValue("Remote, Berlin");
    await expect(popup.locator("#field-description_text")).toHaveValue(/^Acme Robotics builds warehouse robots\./);
    await expect(popup.locator("#identity-external-id")).toHaveText(`${RUN}1`);
    await expect(popup.locator("#identity-url")).toHaveText(href);

    // It names the account before anything is sent, and opening it sent nothing.
    await expect(popup.locator("#account-email")).toHaveText(`Saving as ${OWNER}.`);
    expect(stub?.saves ?? []).toHaveLength(0);

    await popup.locator("#field-job_title").fill("Senior Backend Engineer, Platform");
    await popup.locator("#exact summary").click();
    const shown = JSON.parse((await popup.locator("#exact-body").textContent()) ?? "null");
    expect(shown).toMatchObject({
      source_type: "greenhouse",
      source_url: href,
      external_job_id: `${RUN}1`,
      job_title: "Senior Backend Engineer, Platform",
      company_name: "Acme Robotics",
      location_raw: "Remote, Berlin",
    });

    await popup.locator("#save").click();
    await expect(popup.locator("#outcome-notice")).toContainText("Saved to EasyApply.");
    await expect(popup.locator("#save")).toHaveText("Saved");
    await expect(popup.locator("#save")).toBeDisabled();

    if (stub) {
      expect(stub.saves).toHaveLength(1);
      const [post] = stub.saves;
      // Sent from the extension's service worker, as the proxy's rule expects…
      expect(post?.headers.origin).toBe(`chrome-extension://${EXTENSION_ID}`);
      expect(post?.headers["sec-fetch-site"]).toBe("none");
      // …carrying the web app's own session cookie, set by the web app's page…
      expect(post?.headers.cookie).toMatch(/(^|; )easyapply_session=[0-9a-f]{32}($|;)/);
      // …with no identity of its own…
      expect(post?.headers).not.toHaveProperty("x-dev-user-email");
      expect(post?.headers).not.toHaveProperty("authorization");
      // …and exactly the body the review screen showed.
      expect(JSON.parse(post?.body ?? "")).toEqual(shown);
      expect(stub.jobs.map((job) => job.owner)).toEqual([OWNER]);
    }

    // The job is there for the account that was signed in.
    const [jobPage] = await Promise.all([context.waitForEvent("page"), popup.locator("#open-job").click()]);
    await expect(jobPage.locator("h1")).toContainText("Senior Backend Engineer, Platform", { timeout: 20_000 });

    // Reopened, it says so and offers no second save.
    const again = await openPopup(context, tabId);
    await expect(again.locator("#outcome-notice")).toContainText("from this tab");
    await expect(again.locator("#save")).toBeDisabled();

    // The job page was only read: unchanged, and nothing was ever sent to it.
    expect(await page.evaluate(() => document.documentElement.outerHTML)).toBe(pageBefore);
    expect(jobSiteRequests.filter((request) => request.method !== "GET")).toEqual([]);
  });

  test("saved again from another tab is 'already in EasyApply', not a second job", async ({ context, worker, stub }) => {
    await signIn(context, OWNER);
    const first = await openPosting(context, worker, jobUrl(2));
    const popup = await openPopup(context, first.tabId);
    await popup.locator("#save").click();
    await expect(popup.locator("#outcome-notice")).toContainText("Saved to EasyApply.");

    // The same posting, reached through a tracking link.
    const second = await openPosting(context, worker, `${jobUrl(2)}?gh_src=newsletter`);
    const again = await openPopup(context, second.tabId);
    await expect(again.locator("#save")).toBeEnabled();
    await again.locator("#field-job_title").fill("A different title");
    await again.locator("#save").click();

    const notice = again.locator("#outcome-notice");
    await expect(notice).toContainText("Already in EasyApply.");
    await expect(notice).toContainText("nothing was added or changed");
    await expect(notice).toContainText("differs from what you reviewed in: title");
    if (stub) expect(stub.jobs.filter((job) => job.external_job_id === `${RUN}2`)).toHaveLength(1);
  });
});

test.describe("the session the extension presents is the web app's", () => {
  test("signed out, it offers sign-in and sends nothing; signed in, it saves as that person", async ({
    context,
    worker,
    stub,
  }) => {
    const { tabId } = await openPosting(context, worker, jobUrl(4));
    const signedOut = await openPopup(context, tabId);
    await expect(signedOut.locator("#account-notice")).toContainText("Sign in to EasyApply to save this job.");
    await expect(signedOut.locator("#sign-in")).toHaveAttribute("href", `${WEB_URL}/`);
    await expect(signedOut.locator("#save")).toBeDisabled();
    expect(stub?.saves ?? []).toHaveLength(0);

    const person = `second-${RUN}@ext-e2e.easyapply.test`;
    await signIn(context, person);

    const signedIn = await openPopup(context, tabId);
    await expect(signedIn.locator("#account-email")).toHaveText(`Saving as ${person}.`);
    await signedIn.locator("#save").click();
    await expect(signedIn.locator("#outcome-notice")).toContainText("Saved to EasyApply.");
    if (stub) expect(stub.jobs.filter((job) => job.external_job_id === `${RUN}4`).map((job) => job.owner)).toEqual([person]);
  });

  test("signing out of the web app signs the extension out too", async ({ context, worker }) => {
    await signIn(context, OWNER);
    const { tabId } = await openPosting(context, worker, jobUrl(5));
    await expect((await openPopup(context, tabId)).locator("#account-email")).toHaveText(`Saving as ${OWNER}.`);

    await signOut(context);

    const after = await openPopup(context, tabId);
    await expect(after.locator("#account-notice")).toContainText("Sign in to EasyApply to save this job.");
    await expect(after.locator("#save")).toBeDisabled();
  });
});

test.describe("pages it cannot save", () => {
  test("a board page is not a posting", async ({ context, worker, stub }) => {
    const { tabId } = await openPosting(context, worker, "https://job-boards.greenhouse.io/acmerobotics");
    const popup = await openPopup(context, tabId);
    await expect(popup.locator("#blocked")).toContainText("No single job here.");
    await expect(popup.locator("#save")).toHaveCount(0);
    expect(stub?.requests ?? []).toHaveLength(0);
  });

  test("a posting it cannot read fails explicitly and sends nothing", async ({ context, worker, stub }) => {
    const { tabId } = await openPosting(context, worker, jobUrl(3, "brokenco"));
    const popup = await openPopup(context, tabId);
    await expect(popup.locator("#blocked")).toContainText("Couldn't read this job.");
    await expect(popup.locator("#blocked")).toContainText("job title and job description");
    await expect(popup.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(stub?.requests ?? []).toHaveLength(0);
  });

  test("another job site gets no content script at all", async ({ context, worker }) => {
    // Chrome shows this extension neither the URL nor the title of a tab on a
    // site it has no permission for, so the tab is found as the one that is new.
    const tabIds = () => worker.evaluate(async () => (await chrome.tabs.query({})).map((tab) => tab.id));
    const before = await tabIds();
    const page = await context.newPage();
    await page.goto("https://www.linkedin.com/jobs/view/123");
    const [tabId] = (await tabIds()).filter((id) => !before.includes(id));
    expect(tabId).toBeDefined();
    expect(await worker.evaluate(async (id) => (await chrome.tabs.get(id!)).url, tabId)).toBeUndefined();
    expect(await contentScriptAnswers(worker, tabId!)).toBe(false);

    const popup = await openPopup(context, tabId!);
    await expect(popup.locator("#blocked")).toContainText("Not a supported job page.");
    await expect(popup.locator("#blocked")).toContainText("Greenhouse");
  });
});
