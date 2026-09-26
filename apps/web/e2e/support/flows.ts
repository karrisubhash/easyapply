import { expect, type Page } from "@playwright/test";

import { E2E } from "./env.cjs";

/**
 * The journeys a test needs to have happened before the thing it is about.
 *
 * Every one of these drives the real UI — clicking real buttons, filling real
 * fields, waiting on real state. None of them calls an API directly or writes
 * to the database, because a test whose setup skipped the UI would be asserting
 * about a state the product might not actually be able to reach.
 *
 * They are helpers, not assertions: each waits for the observable state that
 * says the step finished, so a caller does not race the next one. What they do
 * NOT do is assert the step was CORRECT — that belongs in the spec that is
 * about it, or the real coverage would live in a file nobody opens when
 * something fails.
 */

/** The prose the profile carries.
 *
 * Tailoring selects clauses out of this, so the sentences differ deliberately
 * in how well they answer the job below: the first names both technologies the
 * posting wants, the last names neither. That is what makes "did selection
 * actually happen" an assertable question rather than a hopeful one. */
export const RELEVANT_SENTENCE =
  "Built internal tooling in Python and FastAPI for the operations team.";
export const SECOND_SENTENCE = "Migrated the reporting database to PostgreSQL.";
export const IRRELEVANT_SENTENCE = "Organised the annual office relocation.";

export const EXPERIENCE_DESCRIPTION = [
  RELEVANT_SENTENCE,
  SECOND_SENTENCE,
  IRRELEVANT_SENTENCE,
].join(" ");

export const COMPANY = "APTRANSCO";
export const JOB_TITLE = "AI Developer Intern";

export const JOB_DESCRIPTION = `Requirements:
- Strong proficiency in Python
- Experience with PostgreSQL

Preferred:
- Experience with Kubernetes

Responsibilities:
- Build and operate internal tooling
`;

/** Create the profile from the dashboard's own first-run call to action. */
export async function createProfile(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("link", { name: "Create profile" }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await page.getByRole("button", { name: "Create profile" }).click();
  // The page's own heading, which is "My Profile" — the helper waited on a
  // heading that has never existed, so every spec that needed a profile failed
  // here before reaching what it was about.
  await expect(page.getByRole("heading", { name: "My Profile" })).toBeVisible();
}

/**
 * Upload the fixture resume and wait for the worker to finish reading it.
 *
 * Returns the draft version number the pipeline produced. The wait is on the
 * page announcing the draft, which is exactly what a person waits for — not on
 * a timer, and not on a network call the test happens to know about.
 */
export async function uploadResume(page: Page): Promise<number> {
  await page.goto("/resume");

  // The input is visually hidden behind a button, but it carries an accessible
  // name, so the test addresses it the way assistive technology would.
  await page.getByLabel("Resume file").setInputFiles(E2E.fixtureResume);

  await expect(page).toHaveURL(/\/resume\/\d+$/, { timeout: 30_000 });

  const ready = page.getByRole("heading", { name: /Draft version \d+ is ready/i });
  await expect(ready).toBeVisible({ timeout: 60_000 });

  const heading = await ready.textContent();
  const match = heading?.match(/Draft version (\d+) is ready/i);
  if (!match) throw new Error(`could not read the version number from: ${heading}`);
  return Number(match[1]);
}

/**
 * Fill the draft with career data that has prose in it, and confirm it.
 *
 * Through the editor, because the product's own flow is upload → review → EDIT
 * → confirm: the extractor's reading is a proposal, and the description field
 * is what tailoring later selects from.
 */
export async function reviewAndConfirm(page: Page, versionNumber: number): Promise<void> {
  await page.goto(`/profile/versions/${versionNumber}/edit`);
  await expect(page.getByRole("heading", { name: /Editing version/i })).toBeVisible();

  // The extractor may or may not have found an experience. Both are legitimate
  // readings of a PDF, so the helper handles either rather than depending on
  // which one today's structurer produces.
  if ((await page.getByLabel(/^Company/).count()) === 0) {
    await page.getByRole("button", { name: "Add experience" }).click();
  }

  await page.getByLabel(/^Company/).first().fill(COMPANY);
  await page.getByLabel(/^Job title/).first().fill(JOB_TITLE);
  await page.getByLabel("Start date", { exact: true }).first().fill("2024-05-01");
  await page.getByLabel(/^Description/).first().fill(EXPERIENCE_DESCRIPTION);

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page).toHaveURL(new RegExp(`/profile/versions/${versionNumber}$`));

  await confirmVersion(page, versionNumber);
}

/** Take a draft through the confirmation dialog. */
export async function confirmVersion(page: Page, versionNumber: number): Promise<void> {
  await page.getByRole("button", { name: "Confirm version" }).click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByText(`Confirm version ${versionNumber}?`)).toBeVisible();
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();

  await expect(page.getByText("This version is confirmed")).toBeVisible();
}

/** Save a job through the form and land on its detail page. Returns its id. */
export async function createJob(
  page: Page,
  overrides: { title?: string; company?: string; url?: string; description?: string } = {},
): Promise<number> {
  const title = overrides.title ?? "Backend Engineer";

  await page.goto("/jobs/new");
  await page.getByLabel(/^Job title/).fill(title);
  await page.getByLabel(/^Company/).fill(overrides.company ?? "Acme Corp");
  await page
    .getByLabel(/^Posting URL/)
    .fill(overrides.url ?? `https://example.com/jobs/${title.replace(/\W+/g, "-")}`);
  await page.getByLabel(/^Description/).fill(overrides.description ?? JOB_DESCRIPTION);

  await page.getByRole("button", { name: "Save job" }).click();

  await expect(page).toHaveURL(/\/jobs\/\d+$/, { timeout: 30_000 });
  return Number(page.url().match(/\/jobs\/(\d+)$/)![1]);
}

/** Run a match from the match page and wait for its verdicts to render. */
export async function runMatch(page: Page, jobId: number): Promise<void> {
  await page.goto(`/jobs/${jobId}/match`);
  await page.getByRole("button", { name: /Match my profile/i }).click();
  await expect(page.getByText("Requirement coverage")).toBeVisible({ timeout: 45_000 });
}

/**
 * Generate a tailored resume and wait for the run to reach a terminal state.
 *
 * Returns the run id from the URL. The wait is on the review page stating an
 * outcome — approved, refused, or failed — so a caller cannot race a verdict
 * that has not been reached, and a test about one of those three does not have
 * to know which of them this helper saw.
 */
export async function generateTailoredResume(page: Page, jobId: number): Promise<number> {
  await page.goto(`/jobs/${jobId}/tailor`);
  await page.getByRole("button", { name: "Generate resume" }).click();

  // Either the page navigates straight there — a run that finished before the
  // POST answered — or it polls and then offers the link.
  if (!/\/tailored-resumes\/\d+/.test(page.url())) {
    const openLast = page.getByRole("link", { name: /Open the last one/i });
    await expect(openLast).toBeVisible({ timeout: 60_000 });
    await openLast.click();
  }

  await expect(page).toHaveURL(/\/tailored-resumes\/\d+$/, { timeout: 30_000 });
  await expect(
    page.getByText(
      /Every statement traces back to your profile|was not approved|Generation failed/i,
    ),
  ).toBeVisible({ timeout: 60_000 });

  return Number(page.url().match(/\/tailored-resumes\/(\d+)$/)![1]);
}

/**
 * Everything up to and including a confirmed profile version.
 *
 * The precondition for matching and tailoring, and the slowest part of the
 * suite, so it is one function rather than four copies of it.
 */
export async function aConfirmedProfile(page: Page): Promise<number> {
  await createProfile(page);
  const versionNumber = await uploadResume(page);
  await reviewAndConfirm(page, versionNumber);
  return versionNumber;
}
