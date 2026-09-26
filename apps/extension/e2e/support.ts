/**
 * What every browser spec needs: Chromium with the built extension loaded, the
 * job-site pages served from the fixtures at their real https:// addresses,
 * the web app (a recording stub, or a real one — see ./env.ts), and the steps
 * a person takes: open a posting, sign in or out on the web app, open the
 * popup for a tab.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, expect, test as base, type BrowserContext, type Page, type Worker } from "@playwright/test";

import { BUILD_DIR, EXTENSION_ID, FIXTURES, REAL_ENTRA, REAL_WEB_URL, STUB_PORT, WEB_URL } from "./env";
import { startStub, type Stub } from "./stub-server";

export const JOB_HTML = readFileSync(resolve(FIXTURES, "greenhouse-job.html"), "utf8");
export const BOARD_HTML = readFileSync(resolve(FIXTURES, "greenhouse-board.html"), "utf8");
/** The same posting with a full application form, as Greenhouse renders it (test/fixtures/greenhouse-application.html). */
export const APPLICATION_HTML = readFileSync(resolve(FIXTURES, "greenhouse-application.html"), "utf8");
/** A Lever posting and its application page (test/fixtures/lever-*.html). */
export const LEVER_JOB_HTML = readFileSync(resolve(FIXTURES, "lever-job.html"), "utf8");
export const LEVER_APPLY_HTML = readFileSync(resolve(FIXTURES, "lever-apply.html"), "utf8");
// A posting page whose layout the adapter does not recognise.
export const UNREADABLE_HTML = JOB_HTML.replace(/<main[\s\S]*<\/main>/, "<main><p>Loading…</p></main>");

/** The board whose postings are served with the full application form. */
export const APPLICATION_BOARD = "acmeapply";

export interface Fixtures {
  stub: Stub | null;
  context: BrowserContext;
  worker: Worker;
  /** Every request a page made to a job site, to prove nothing was submitted there. */
  jobSiteRequests: { method: string; url: string }[];
}

export const test = base.extend<Fixtures>({
  // Auto: listening before any test runs, whether or not the test reads it.
  stub: [
    async ({}, use) => {
      const stub = REAL_WEB_URL ? null : await startStub(STUB_PORT, EXTENSION_ID);
      await use(stub);
      await stub?.close();
    },
    { auto: true },
  ],

  jobSiteRequests: async ({}, use) => {
    await use([]);
  },

  context: async ({ jobSiteRequests }, use) => {
    const context = await chromium.launchPersistentContext("", {
      channel: "chromium",
      headless: true,
      acceptDownloads: true,
      args: [`--disable-extensions-except=${BUILD_DIR}`, `--load-extension=${BUILD_DIR}`],
    });

    // Job-site pages come from the fixtures, at their real addresses, so it is
    // Chrome's own matching of the manifest that decides where the content
    // script runs.
    await context.route("https://job-boards.greenhouse.io/**", async (route) => {
      const url = new URL(route.request().url());
      jobSiteRequests.push({ method: route.request().method(), url: url.href });
      const html = url.pathname.startsWith("/brokenco/")
        ? UNREADABLE_HTML
        : url.pathname.startsWith(`/${APPLICATION_BOARD}/`) && /\/jobs\/\d+$/.test(url.pathname)
          ? APPLICATION_HTML
          : /\/jobs\/\d+$/.test(url.pathname)
            ? JOB_HTML
            : BOARD_HTML;
      await route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html });
    });
    await context.route("https://jobs.lever.co/**", async (route) => {
      const url = new URL(route.request().url());
      jobSiteRequests.push({ method: route.request().method(), url: url.href });
      const html = url.pathname.endsWith("/apply") ? LEVER_APPLY_HTML : LEVER_JOB_HTML;
      await route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html });
    });
    await context.route("https://www.linkedin.com/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<title>A job</title><h1>A LinkedIn job</h1>" }),
    );
    await context.route("https://example.invalid/**", (route) => route.abort());

    await use(context);
    await context.close();
  },

  worker: async ({ context }, use) => {
    const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker"));
    await use(worker);
  },
});

export { expect };

export async function tabIdOf(worker: Worker, href: string): Promise<number> {
  const id = await worker.evaluate(
    async (url) => (await chrome.tabs.query({})).find((tab) => tab.url === url)?.id,
    href,
  );
  if (id === undefined) throw new Error(`no tab at ${href}`);
  return id;
}

/** Whether the content script in that tab answers — i.e. Chrome injected it. */
export async function contentScriptAnswers(worker: Worker, tabId: number): Promise<boolean> {
  return worker.evaluate(
    (id) =>
      chrome.tabs
        .sendMessage(id, { type: "easyapply/extract" })
        .then(() => true)
        .catch(() => false),
    tabId,
  );
}

export async function openPosting(context: BrowserContext, worker: Worker, href: string): Promise<{ page: Page; tabId: number }> {
  const page = await context.newPage();
  await page.goto(href);
  const tabId = await tabIdOf(worker, href);
  // The content script runs at document_idle; a person opens the popup after that.
  await expect.poll(() => contentScriptAnswers(worker, tabId)).toBe(true);
  return { page, tabId };
}

/**
 * Sign this browser in to the web app, the way a person does: on its own page,
 * in a tab of the same profile the extension lives in. Nothing is handed to the
 * extension — it just starts presenting the same cookie.
 */
export async function signIn(context: BrowserContext, email: string): Promise<void> {
  const page = await context.newPage();
  await page.goto(`${WEB_URL}/`);
  if (REAL_ENTRA) {
    // Real sign-in: "Sign in with Microsoft", then the provider's account picker.
    await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
    await expect(page.getByRole("heading", { name: "Pick an account" })).toBeVisible();
    await page.getByLabel("Another account").fill(email);
    await page.getByRole("button", { name: "Continue" }).click();
  } else {
    await page.getByLabel("Email").fill(email);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.getByTitle(email)).toBeVisible({ timeout: 20_000 });
  await page.close();
}

export async function signOut(context: BrowserContext): Promise<void> {
  const page = await context.newPage();
  await page.goto(`${WEB_URL}/`);
  await page.getByRole("button", { name: "Sign out" }).click();
  // Under real sign-in, through the provider's end-session endpoint and back to /signed-out.
  await expect(
    page.getByRole("heading", { name: REAL_ENTRA ? "Sign in to EasyApply" : "Sign in" }),
  ).toBeVisible({ timeout: 20_000 });
  await page.close();
}

/** The review screen for a tab, opened as a page (a test cannot click the toolbar). */
export async function openPopup(context: BrowserContext, tabId: number): Promise<Page> {
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${EXTENSION_ID}/popup.html?tabId=${tabId}`);
  await expect(popup.locator("#review, #blocked")).toBeVisible();
  return popup;
}
