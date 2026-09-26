import AxeBuilder from "@axe-core/playwright";
import type { Browser, BrowserContext, Page } from "@playwright/test";

import { E2E, ENTRA_API_URL, ENTRA_URL } from "../support/env.cjs";
import { expect, resetDatabase, test } from "../support/fixtures";
import { aConfirmedProfile, createJob, createProfile, generateTailoredResume, runMatch } from "../support/flows";

/**
 * Real sign-in: the web server in AUTH_MODE=entra, its API in AUTH_MODE=entra,
 * and a local OpenID provider shaped like Microsoft Entra External ID
 * (support/mock-entra.mjs) in place of Microsoft. Everything EasyApply does is
 * real — the authorization-code flow with PKCE, the callback, the ID token
 * validation, the session cookie, the per-request assertion to the API, the
 * provisioning of an account from the Entra object id. Only the provider is
 * local. Nothing here says anything about a real Entra tenant.
 */

const TAG_HEADER = "X-EasyApply-Session";

async function entraContext(browser: Browser): Promise<BrowserContext> {
  return browser.newContext({ baseURL: ENTRA_URL });
}

function user(id: string) {
  const found = E2E.entraUsers.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no test user ${id}`);
  return found;
}

/** Press "Sign in with Microsoft" and pick an account at the provider. */
async function signInWithMicrosoft(page: Page, id: string, from = "/"): Promise<void> {
  if (!page.url().startsWith(ENTRA_URL) || new URL(page.url()).pathname !== from) await page.goto(from);
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
  await expect(page.getByRole("heading", { name: "Pick an account" })).toBeVisible();
  await page.getByRole("button", { name: new RegExp(`^${user(id).name}`) }).click();
  await expect(page).toHaveURL(new RegExp(`^${ENTRA_URL}`));
}

async function expectSignedInAs(page: Page, label: string): Promise<void> {
  await expect(page.locator("#signed-in-account")).toHaveText(label);
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  // Through the provider's end-session endpoint and back to /signed-out.
  await expect(page).toHaveURL(`${ENTRA_URL}/signed-out`);
  await expect(page.getByText("You signed out of EasyApply.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
}

async function tagOf(page: Page): Promise<string> {
  const session = await (await page.request.get(`${ENTRA_URL}/api/auth/session`)).json();
  expect(session.authenticated).toBe(true);
  return session.sessionTag as string;
}

test.beforeEach(() => {
  resetDatabase();
});

test.describe("real sign-in with Microsoft Entra External ID (local provider)", () => {
  test("signed out, every page offers Sign in with Microsoft, and the proxy refuses rather than guessing", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    for (const path of ["/", "/jobs", "/profile"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: "Sign in to EasyApply" })).toBeVisible();
      await expect(page.getByText("Not signed in")).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
    }
    const data = await page.request.get(`${ENTRA_URL}/api/backend/jobs`);
    expect(data.status()).toBe(401);
    expect((await data.json()).code).toBe("session_required");
    expect(await (await page.request.get(`${ENTRA_URL}/api/auth/session`)).json()).toEqual({
      mode: "entra",
      authenticated: false,
      email: null,
      name: null,
      sessionTag: null,
    });

    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
    await context.close();
  });

  test("signing in returns to the page it began on, as the person, with an HttpOnly session and no tokens", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    await page.goto("/jobs");
    await signInWithMicrosoft(page, "ada", "/jobs");
    await expect(page).toHaveURL(`${ENTRA_URL}/jobs`);
    await expectSignedInAs(page, user("ada").email!);

    const cookies = await context.cookies();
    const session = cookies.find((cookie) => cookie.name === "easyapply_session");
    expect(session).toMatchObject({ httpOnly: true, sameSite: "Lax", path: "/" });
    expect(session!.value).toMatch(/^v2\./);
    // The transaction cookie is gone, and nothing the provider issued is kept anywhere the page can reach.
    expect(cookies.filter((cookie) => cookie.name.startsWith("easyapply_oidc_"))).toEqual([]);
    const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    expect(storage).not.toMatch(/eyJ/);
    expect(await page.evaluate(() => document.cookie)).toBe("");

    const body = await (await page.request.get(`${ENTRA_URL}/api/auth/session`)).json();
    expect(body).toEqual({
      mode: "entra",
      authenticated: true,
      email: user("ada").email,
      name: user("ada").name,
      sessionTag: expect.stringMatching(/^[A-Za-z0-9_-]{22}$/),
    });
    await context.close();
  });

  test("two people take turns in one browser — and the same email is not the same account", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();

    await signInWithMicrosoft(page, "ada");
    await createProfile(page);
    await createJob(page, { title: "Ada's Job" });
    await signOut(page);

    // Same email address, different Entra user object: a different account.
    await signInWithMicrosoft(page, "ada-twin");
    await expectSignedInAs(page, user("ada-twin").email!);
    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: /Ada's Job/ })).toHaveCount(0);
    expect(((await (await page.request.get(`${ENTRA_URL}/api/backend/jobs`)).json()) as unknown[]).length).toBe(0);
    expect((await page.request.get(`${ENTRA_URL}/api/backend/profile`)).status()).toBe(404);
    await signOut(page);

    await signInWithMicrosoft(page, "grace");
    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: /Ada's Job/ })).toHaveCount(0);
    await signOut(page);

    // And Ada, back again, has everything she left.
    await signInWithMicrosoft(page, "ada");
    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: /Ada's Job/ }).first()).toBeVisible();
    await context.close();
  });

  test("User A's whole journey under real sign-in, then User B in the same browser sees none of it", async ({ browser }) => {
    test.setTimeout(300_000);
    const context = await entraContext(browser);
    const page = await context.newPage();

    // A: sign in → profile → resume → confirmed version → job → match → tailored resume.
    await signInWithMicrosoft(page, "ada");
    await aConfirmedProfile(page);
    const jobId = await createJob(page, { title: "Ada's Job" });
    await runMatch(page, jobId);
    const runId = await generateTailoredResume(page, jobId);

    const asA = page.request;
    const resumes = (await (await asA.get(`${ENTRA_URL}/api/backend/resumes`)).json()) as { id: number }[];
    const versions = (await (await asA.get(`${ENTRA_URL}/api/backend/profile/versions`)).json()) as { version_number: number }[];
    const matches = (await (await asA.get(`${ENTRA_URL}/api/backend/jobs/${jobId}/matches`)).json()) as { id: number }[];
    expect(resumes.length).toBeGreaterThan(0);
    expect(versions.length).toBeGreaterThan(0);
    expect(matches.length).toBeGreaterThan(0);
    const aOwns = [
      "profile",
      `profile/versions/${versions[0]!.version_number}`,
      `resumes/${resumes[0]!.id}`,
      `resumes/${resumes[0]!.id}/result`,
      `jobs/${jobId}`,
      `jobs/${jobId}/requirements`,
      `matches/${matches[0]!.id}`,
      `tailored-resumes/${runId}`,
      `tailored-resumes/${runId}/artifact`,
    ];
    for (const path of aOwns) expect((await asA.get(`${ENTRA_URL}/api/backend/${path}`)).status(), path).toBeLessThan(400);

    await page.goto("/");
    await signOut(page);

    // B, same browser: every list is empty and every one of A's records is a 404.
    await signInWithMicrosoft(page, "grace");
    await expectSignedInAs(page, user("grace").email!);
    const asB = page.request;
    for (const list of ["resumes", "jobs", "tailored-resumes", "profile/versions"]) {
      const response = await asB.get(`${ENTRA_URL}/api/backend/${list}`);
      if (response.status() === 404) continue; // no profile yet: versions are not there at all
      expect(await response.json(), list).toEqual([]);
    }
    for (const path of aOwns) {
      const response = await asB.get(`${ENTRA_URL}/api/backend/${path}`);
      expect(response.status(), path).toBe(404);
      expect(response.headers()["content-disposition"], path).toBeUndefined();
    }
    for (const path of ["/jobs", "/resume", "/tailored-resumes"]) {
      await page.goto(path);
      await expect(page.getByText("Ada's Job")).toHaveCount(0);
    }
    await context.close();
  });

  test("a stale tab stops showing the previous account, and can never act for the new one", async ({ browser }) => {
    const context = await entraContext(browser);
    const stale = await context.newPage();
    await signInWithMicrosoft(stale, "ada");
    await createJob(stale, { title: "Ada's Job" });
    await stale.goto("/jobs");
    await expect(stale.getByRole("link", { name: /Ada's Job/ }).first()).toBeVisible();
    const adasTag = await tagOf(stale);

    // In another tab of the same browser, Grace signs in over Ada's session
    // (no sign-out first: the cookie is simply replaced by the callback).
    const other = await context.newPage();
    await other.goto("/");
    await expectSignedInAs(other, user("ada").email!);
    await other.evaluate(() => {
      const form = document.createElement("form");
      form.method = "post";
      form.action = "/api/auth/sign-in";
      document.body.append(form);
      form.submit();
    });
    await expect(other.getByRole("heading", { name: "Pick an account" })).toBeVisible();
    await other.getByRole("button", { name: new RegExp(`^${user("grace").name}`) }).click();
    await expectSignedInAs(other, user("grace").email!);

    // The stale tab is told, reloads for Grace, and says why. Ada's job is gone from it.
    await stale.bringToFront();
    await expect(stale.getByText(/signed in to a different EasyApply account in another tab/)).toBeVisible();
    await expectSignedInAs(stale, user("grace").email!);
    await expect(stale.getByRole("link", { name: /Ada's Job/ })).toHaveCount(0);

    // A request still carrying Ada's tag is refused, not carried out as Grace.
    const write = await other.request.post(`${ENTRA_URL}/api/backend/jobs`, {
      headers: { [TAG_HEADER]: adasTag, Origin: ENTRA_URL, "Content-Type": "application/json" },
      data: { job_title: "Written as the wrong person", company: "X", source_url: "https://example.com/wrong", description: "x" },
    });
    expect(write.status()).toBe(409);
    expect((await write.json()).code).toBe("session_changed");
    expect(((await (await other.request.get(`${ENTRA_URL}/api/backend/jobs`)).json()) as unknown[]).length).toBe(0);

    // Signing out in one tab: the other says so, and shows the sign-in — not an "expired" session.
    await signOut(other);
    await expect(stale.getByText("You signed out of EasyApply in another tab.")).toBeVisible();
    await expect(stale.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
    await expect(stale.getByText(user("grace").email!)).toHaveCount(0);
    await context.close();
  });

  test("a session that ends mid-edit keeps the form; signing in again as the same person carries on", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    await signInWithMicrosoft(page, "ada");
    await page.goto("/jobs/new");
    await page.getByLabel(/^Job title/).fill("Kept Through Expiry");
    await page.getByLabel(/^Company/).fill("Acme Corp");
    await page.getByLabel(/^Posting URL/).fill("https://example.com/jobs/kept");
    await page.getByLabel(/^Description/).fill("Requirements:\n- Python");

    // The session ends (as it does when its lifetime runs out: the browser drops the cookie).
    await context.clearCookies();
    await page.getByRole("button", { name: "Save job" }).click();
    await expect(page.locator("#session-ended")).toContainText("Your EasyApply session expired. Sign in again.");
    await expect(page.getByLabel(/^Job title/)).toHaveValue("Kept Through Expiry");

    // Signing in again in a new tab, as the same person, keeps the account tag…
    const again = await context.newPage();
    await signInWithMicrosoft(again, "ada");
    await expectSignedInAs(again, user("ada").email!);
    await page.bringToFront();
    await expect(page.locator("#session-ended")).toHaveCount(0);

    // …so the kept form saves, into Ada's account.
    await page.getByRole("button", { name: "Save job" }).click();
    await expect(page).toHaveURL(/\/jobs\/\d+$/, { timeout: 30_000 });
    await again.goto("/jobs");
    await expect(again.getByRole("link", { name: /Kept Through Expiry/ }).first()).toBeVisible();
    await context.close();
  });

  test("an expired session never throws the page away: it says so, and the next load is the sign-in", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    await signInWithMicrosoft(page, "ada");
    await page.goto("/");
    await context.clearCookies();
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Jobs" }).click();
    const notice = page.locator("#session-ended");
    await expect(notice).toContainText("Your EasyApply session expired. Sign in again.");
    await expect(notice.getByRole("link", { name: "Sign in in a new tab" })).toHaveAttribute("target", "_blank");
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
    await context.close();
  });

  test("a cancelled sign-in, a forged callback and a token Entra would never send all end signed out, with a reason", async ({
    browser,
  }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();

    await page.goto("/");
    await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText("Sign-in was cancelled. Nothing was changed.")).toBeVisible();

    // A callback for a sign-in this browser never started (login CSRF / tampered state).
    await page.goto(`/api/auth/callback?code=forged&state=${"A".repeat(43)}`);
    await expect(page.getByText("That sign-in took too long or was already used. Sign in again.")).toBeVisible();

    // A validly signed ID token for another application (wrong audience).
    await signInWithMicrosoft(page, "wrong-audience");
    await expect(page.getByText("Signing in with Microsoft did not work. Try again.")).toBeVisible();
    await expect(page.getByText("Not signed in")).toBeVisible();

    expect((await context.cookies()).find((cookie) => cookie.name === "easyapply_session")).toBeUndefined();
    await context.close();
  });

  test("a forged session cookie and identity-shaped headers are nobody, at the web app and at the API", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    const forgedPayload = Buffer.from(
      JSON.stringify({ typ: "entra", iss: "x", tid: E2E.entraTenantId, oid: user("ada").oid, email: null, name: null, op: true, sid: "A".repeat(22), iat: 1, exp: 9999999999 }),
    ).toString("base64url");
    await context.addCookies([{ name: "easyapply_session", value: `v2.${forgedPayload}.AAAA`, url: ENTRA_URL }]);
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();

    const headers = { "X-Dev-User-Email": user("ada").email!, "X-User-ID": "1", Authorization: "Bearer forged" };
    expect((await page.request.get(`${ENTRA_URL}/api/backend/profile`, { headers })).status()).toBe(401);
    // Straight at the API, past the web app: the development header and a forged assertion are refused.
    const directly: Record<string, string>[] = [{ "X-Dev-User-Email": user("ada").email! }, { Authorization: "Bearer forged" }, {}];
    for (const direct of directly) {
      const response = await page.request.get(`${ENTRA_API_URL}/api/v1/profile`, { headers: direct });
      expect(response.status()).toBe(401);
    }

    // Signed in as Grace, claiming to be Ada changes nothing.
    await context.clearCookies();
    await signInWithMicrosoft(page, "grace");
    await createJob(page, { title: "Grace's Job" });
    const asGrace = await page.request.get(`${ENTRA_URL}/api/backend/jobs`, { headers: { "X-Dev-User-Email": user("ada").email! } });
    expect(asGrace.status()).toBe(200);
    expect(((await asGrace.json()) as { job_title: string }[]).map((job) => job.job_title)).toEqual(["Grace's Job"]);
    await context.close();
  });

  test("the operator pages exist only for holders of the operator role", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();

    await signInWithMicrosoft(page, "ada");
    const response = await page.goto("/internal/resume-processing");
    expect(response?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "Resume processing" })).toHaveCount(0);
    const feed = await page.request.get(`${ENTRA_URL}/api/backend/internal/resume-processing/attention`);
    expect(feed.status()).toBe(404);
    // Nor by a path whose segments decode into a way round the check.
    for (const sneaky of ["jobs/..%2Finternal/resume-processing/attention", "jobs/..%2F..%2F..%2Fopenapi.json"]) {
      expect((await page.request.get(`${ENTRA_URL}/api/backend/${sneaky}`)).status(), sneaky).toBe(404);
    }
    // The API refuses the token without the role even when called directly.
    const direct = await page.request.get(`${ENTRA_API_URL}/api/v1/internal/resume-processing/attention`, {
      headers: { "X-Operator-Token": E2E.operatorToken },
    });
    expect(direct.status()).toBe(403);
    await page.goto("/");
    await signOut(page);

    await signInWithMicrosoft(page, "olive");
    await page.goto("/internal/resume-processing");
    await expect(page.getByRole("heading", { name: "Resume processing" })).toBeVisible();
    expect((await page.request.get(`${ENTRA_URL}/api/backend/internal/resume-processing/attention`)).status()).toBe(200);
    await context.close();
  });

  test("an account the provider gives no email and no name is still signed in, and says so plainly", async ({ browser }) => {
    const context = await entraContext(browser);
    const page = await context.newPage();
    await signInWithMicrosoft(page, "nameless");
    await expectSignedInAs(page, "your EasyApply account");
    await createProfile(page);
    await context.close();
  });
});
