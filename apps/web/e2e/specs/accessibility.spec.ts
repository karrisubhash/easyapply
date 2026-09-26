import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "../support/fixtures";
import { aConfirmedProfile, createJob, generateTailoredResume, runMatch } from "../support/flows";

/**
 * Every screen of the journey, audited where it is populated.
 *
 * Auditing empty states would be auditing the shell: the violations this found
 * were both on rendered DATA — a confidence figure on a citation and the badge
 * on an unmet requirement — and neither page has either until the pipeline has
 * run. So the suite pays for the whole journey once and then audits.
 *
 * Scoped to the WCAG 2 A and AA rules rather than to everything axe knows.
 * `heading-order` is tagged `best-practice`, and it fires on every card in the
 * product because `CardTitle` renders an `h3` under the page's `h1`; that is a
 * design-system decision to make deliberately, not something to let a release
 * gate decide by accident. What is gated here is what WCAG actually requires.
 */

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function audit(page: Page, path: string) {
  await page.goto(path);
  // The audit must see the page with its data on it, not its loading state:
  // a skeleton has no citations and no badges to get wrong.
  await expect(page.getByRole("status")).toHaveCount(0);

  const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();

  // Named in the failure, because "1 violation" sends the reader to a trace.
  const summary = results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact}) on ${violation.nodes.length}: ` +
      `${violation.nodes[0]?.failureSummary?.replace(/\s+/g, " ").slice(0, 200)}`,
  );
  expect(summary, `${path} has WCAG A/AA violations`).toEqual([]);
}

test.describe("the journey meets WCAG 2 A and AA", () => {
  test("every screen, with real data on it", async ({ page }) => {
    const versionNumber = await aConfirmedProfile(page);
    const jobId = await createJob(page);
    await runMatch(page, jobId);
    const runId = await generateTailoredResume(page, jobId);

    for (const path of [
      "/",
      "/profile",
      "/profile/versions",
      `/profile/versions/${versionNumber}`,
      "/resume",
      "/jobs",
      "/jobs/new",
      `/jobs/${jobId}`,
      `/jobs/${jobId}/match`,
      `/jobs/${jobId}/tailor`,
      "/tailored-resumes",
      `/tailored-resumes/${runId}`,
    ]) {
      await audit(page, path);
    }
  });

  test("every row action says which row it acts on", async ({ page }) => {
    /**
     * Not an axe rule — axe reads a link in its document context, where "View"
     * beside a job title is unambiguous. A screen reader's link list is not that
     * context: it reads the accessible names alone, and four rows of "View" name
     * nothing. WCAG 2.4.4 is about exactly that reading.
     *
     * The visible label stays short; the accessible name carries the row.
     */
    await aConfirmedProfile(page);
    await createJob(page, { title: "Backend Engineer" });
    await createJob(page, {
      title: "Platform Engineer",
      company: "Globex",
      url: "https://example.com/jobs/platform",
    });

    await page.goto("/jobs");
    const rowLinks = page.getByRole("link", { name: /^View / });
    // `evaluateAll` does not auto-wait: it answers with whatever matches at the
    // moment it runs, and this listing is fetched after the shell paints.
    await expect(rowLinks).toHaveCount(2);

    const names = await rowLinks.evaluateAll((links) =>
      links.map((link) => link.getAttribute("aria-label")),
    );

    expect(names).toContain("View Backend Engineer at Acme Corp");
    expect(names).toContain("View Platform Engineer at Globex");
    expect(new Set(names).size).toBe(names.length);
  });
});
