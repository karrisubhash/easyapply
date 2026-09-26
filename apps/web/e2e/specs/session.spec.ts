import AxeBuilder from "@axe-core/playwright";
import type { Browser, Page } from "@playwright/test";

import { E2E, OWNER_URL, SESSION_URL } from "../support/env.cjs";
import { expect, resetDatabase, test } from "../support/fixtures";
import { createJob } from "../support/flows";

/**
 * Identity decided per browser: the web server in AUTH_MODE=dev-session.
 *
 * The owner/stranger servers make two people by being two servers. This one
 * makes them the way real sign-in will — one server, a session per browser —
 * and so exercises the identity seam the web app and the Chrome extension
 * share (docs/authentication.md): the layout, the proxy and the session routes
 * all asking `resolveIdentity`, and admission untouched in front of it.
 */

async function sessionPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ baseURL: SESSION_URL });
  return context.newPage();
}

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTitle(email)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

test.beforeEach(() => {
  resetDatabase();
});

test.describe("a server that decides identity per browser", () => {
  test("signed out, every page is the sign-in form, and the proxy refuses rather than guessing", async ({
    browser,
  }) => {
    const page = await sessionPage(browser);

    for (const path of ["/", "/jobs", "/profile"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
      await expect(page.getByText("Not signed in")).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
    }
    // The form offers the configured address; it does not sign anyone in as it.
    await expect(page.getByLabel("Email")).toHaveValue(E2E.sessionSuggestedEmail);

    const data = await page.request.get(`${SESSION_URL}/api/backend/jobs`);
    expect(data.status()).toBe(401);
    expect(await data.json()).toEqual({ detail: "You are not signed in to EasyApply.", code: "session_required" });

    const session = await page.request.get(`${SESSION_URL}/api/auth/session`);
    expect(await session.json()).toEqual({ mode: "dev-session", authenticated: false, email: null, name: null, sessionTag: null });

    // The one screen every signed-out visitor sees meets the same bar as the rest.
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(results.violations.map((violation) => violation.id)).toEqual([]);

    await page.context().close();
  });

  test("two people take turns in one browser, and each sees only their own work", async ({ browser }) => {
    const page = await sessionPage(browser);
    const first = "first@e2e.easyapply.test";
    const second = "second@e2e.easyapply.test";

    await signIn(page, first);
    const jobId = await createJob(page);
    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: /Backend Engineer/i }).first()).toBeVisible();
    await signOut(page);

    await signIn(page, second);
    await page.goto("/jobs");
    await expect(page.getByText("No jobs yet")).toBeVisible();
    await page.goto(`/jobs/${jobId}`);
    await expect(page.getByText("That job does not exist")).toBeVisible();
    await expect(page.getByText(/Acme Corp/)).toHaveCount(0);
    await signOut(page);

    // The first person's work was waiting for them, not lost.
    await signIn(page, first);
    await page.goto(`/jobs/${jobId}`);
    await expect(page.getByText(/Acme Corp/).first()).toBeVisible();

    await page.context().close();
  });

  test("a cookie this server did not sign is no session", async ({ browser }) => {
    const page = await sessionPage(browser);
    await page.context().addCookies([
      {
        name: "easyapply_session",
        // Well-formed, naming someone, signed with nothing this server knows.
        value: `v1.${Buffer.from(JSON.stringify({ sub: "victim@e2e.easyapply.test", iat: 1, exp: 4_102_444_800 })).toString("base64url")}.AAAA`,
        url: SESSION_URL,
      },
    ]);
    await page.goto("/jobs");
    await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
    expect((await page.request.get(`${SESSION_URL}/api/backend/jobs`)).status()).toBe(401);
    await page.context().close();
  });

  test("a development-mode server ignores the session cookie entirely", async ({ browser }) => {
    // Cookies are scoped to a host, not a port: signing in on :3102 puts the
    // cookie on 127.0.0.1, and the browser sends it to :3100 too. That server
    // runs AUTH_MODE=development and must go on being its configured person.
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`${SESSION_URL}/`);
    await page.getByLabel("Email").fill("someone@e2e.easyapply.test");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTitle("someone@e2e.easyapply.test")).toBeVisible();

    await page.goto(`${OWNER_URL}/`);
    await expect(page.getByTitle(E2E.ownerEmail)).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
    const session = await page.request.get(`${OWNER_URL}/api/auth/session`);
    expect(await session.json()).toEqual({
      mode: "development",
      authenticated: true,
      email: E2E.ownerEmail,
      name: null,
      sessionTag: expect.stringMatching(/^[A-Za-z0-9_-]{22}$/),
    });
    await context.close();
  });
});
