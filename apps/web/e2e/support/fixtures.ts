import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import { test as base, expect, type Page } from "@playwright/test";

import { API_DIR, E2E, PYTHON, STRANGER_URL } from "./env.cjs";

/**
 * The suite's shared machinery.
 *
 * Two things every test needs and neither should re-invent: a database that
 * starts empty, and a way to be somebody else.
 */

const DB_ADMIN = resolve(API_DIR, "../../apps/web/e2e/support/db_admin.py");

/** Empty every table. Fast enough to run before each test. */
export function resetDatabase(): void {
  execFileSync(PYTHON, [DB_ADMIN, "truncate"], {
    cwd: API_DIR,
    stdio: "pipe",
    env: { ...process.env, E2E_DATABASE_URL: E2E.databaseUrl },
  });
}

/**
 * Produce a tailored resume that grounding genuinely refuses, and return its id.
 *
 * Runs the real pipeline with a generator that invents a number — see
 * `db_admin.py::seed_grounding_failure` for why this is the only way to reach
 * that screen without enabling the AI generator, and for what is and is not
 * simulated. The verdict is computed by the real validator; the browser then
 * loads the page through the real API.
 */
export function seedGroundingFailure(jobId: number): number {
  const output = execFileSync(
    PYTHON,
    [DB_ADMIN, "seed-grounding-failure", "--email", E2E.ownerEmail, "--job-id", String(jobId)],
    {
      cwd: API_DIR,
      stdio: "pipe",
      env: { ...process.env, DATABASE_URL: E2E.databaseUrl, E2E_DATABASE_URL: E2E.databaseUrl },
    },
  );
  return Number(output.toString().trim());
}

export const test = base.extend<{
  /** A second browser context talking to the OTHER web server, and therefore
   *  authenticated as a different person. Real boundary, not a spoofed header. */
  stranger: Page;
}>({
  // Before every test, not after: a failed test leaves its rows behind for
  // inspection, and the next test still starts clean.
  page: async ({ page }, use) => {
    resetDatabase();
    await use(page);
  },

  stranger: async ({ browser }, use) => {
    const context = await browser.newContext({ baseURL: STRANGER_URL });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect };
