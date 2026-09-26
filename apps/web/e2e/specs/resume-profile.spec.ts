import { E2E } from "../support/env.cjs";
import { expect, test } from "../support/fixtures";
import { createProfile } from "../support/flows";

/**
 * A real resume, through the real pipeline, onto the real screen.
 *
 * This exists because a green suite proved the wrong thing. Every stage
 * reported success while the profile it produced was nearly empty: one job read
 * as twelve unmappable fragments, every project and certification dropped, and
 * the candidate's name, summary and links lost before the AI was ever asked
 * about them. The run said `completed`, so nothing failed.
 *
 * So these assertions are about the CONTENT a person sees, not about the
 * pipeline reaching a terminal state. Each one names a fact the uploaded
 * document states in so many words, and the last of them names facts it does
 * NOT state — because a reading that invents is worse than one that misses.
 *
 * The document is the evaluation dataset's resume, built by the same function
 * the pytest golden test uses, so the browser and the unit suite are arguing
 * about one document rather than two.
 */
test.describe("a resume becomes a profile a person can read", () => {
  test("every section of the document reaches the screen", async ({ page }) => {
    await createProfile(page);

    await page.goto("/resume");
    await page.getByLabel("Resume file").setInputFiles(E2E.goldenResume);
    await expect(page).toHaveURL(/\/resume\/\d+$/, { timeout: 30_000 });

    const ready = page.getByRole("heading", { name: /Draft version \d+ is ready/i });
    await expect(ready).toBeVisible({ timeout: 60_000 });

    const heading = await ready.textContent();
    const versionNumber = Number(heading?.match(/Draft version (\d+) is ready/i)?.[1]);
    expect(versionNumber).toBeGreaterThan(0);

    await page.goto(`/profile/versions/${versionNumber}`);

    // --- who the resume says this is ---------------------------------------
    // None of this had anywhere to go before: no schema field, no column, no
    // section on the page.
    await expect(page.getByText("ALEX MORGAN")).toBeVisible();
    await expect(page.getByText(/alex\.morgan@example\.com/)).toBeVisible();
    await expect(page.getByText(/\+44 7700 900123/)).toBeVisible();
    await expect(page.getByText(/Bristol, United Kingdom/).first()).toBeVisible();

    // --- the summary, in the candidate's own words -------------------------
    await expect(
      page.getByText(/Backend engineer focused on distributed systems/),
    ).toBeVisible();

    // --- links, which live in the PDF's annotations and not in its text ----
    await expect(page.getByRole("link", { name: "LinkedIn" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/alex-morgan-example/",
    );
    await expect(page.getByRole("link", { name: "Portfolio" })).toHaveAttribute(
      "href",
      "https://alexmorgan.example.com",
    );

    // --- both roles, with their employers, dates and bullets ---------------
    await expect(page.getByText("Senior Backend Engineer")).toBeVisible();
    await expect(page.getByText("Northwind Logistics")).toBeVisible();
    await expect(page.getByText("Backend Engineer", { exact: true })).toBeVisible();
    await expect(page.getByText("Cavendish Health")).toBeVisible();

    // An open-ended role reads as current rather than as a missing date.
    await expect(page.getByText(/March 2022 — Present/)).toBeVisible();

    // The bullets are the substance of a role, and a line the page wrapped is
    // still one sentence.
    await expect(
      page.getByText(/cutting p99 latency by 63% across 4M daily events/),
    ).toBeVisible();

    // --- education, with the grade the resume states ------------------------
    await expect(page.getByText(/University of Bristol/)).toBeVisible();
    await expect(page.getByText(/First Class Honours/)).toBeVisible();

    // --- projects, with their technologies ---------------------------------
    await expect(page.getByText("Ledger Reconciliation Engine")).toBeVisible();
    await expect(page.getByText("Observability Starter Kit")).toBeVisible();
    // All three technologies the document lists for this project, ordered by
    // name rather than as the resume wrote them: `project_technologies` is a
    // child table with no ordinal column and the relationship orders by name
    // (models/project.py), so a stable alphabetical read is what the schema
    // promises. Asserting the document's own order would assert something it is
    // deliberately not built to keep.
    await expect(page.getByText("Celery, PostgreSQL, Python")).toBeVisible();

    // --- certifications -----------------------------------------------------
    await expect(page.getByText(/AWS Certified Solutions Architect/)).toBeVisible();
    await expect(page.getByText(/Certified Kubernetes Administrator/)).toBeVisible();

    // --- skills, with the row label as a category rather than glued on ------
    await expect(page.getByText("Python", { exact: true })).toBeVisible();
    await expect(page.getByText("Load Testing", { exact: true })).toBeVisible();
    await expect(page.getByText("Languages:Python")).toHaveCount(0);

    // --- and nothing the document does not say ------------------------------
    // Each of these was produced by the pipeline before the fix.
    await expect(page.getByText("latency by 63% across 4M daily events.", { exact: true }))
      .toHaveCount(0);
    await expect(page.getByText("United Kingdom", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/PyCon/)).toHaveCount(0);
    await expect(page.getByText("This version has no career data yet")).toHaveCount(0);
  });

  test("the review screen reports nothing it could not use", async ({ page }) => {
    /**
     * The unmapped list is how the pipeline admits it dropped something. On this
     * document it should be empty: every item the resume states is one the
     * profile schema can hold, and an entry here means something was lost.
     */
    await createProfile(page);

    await page.goto("/resume");
    await page.getByLabel("Resume file").setInputFiles(E2E.goldenResume);
    await expect(page).toHaveURL(/\/resume\/\d+$/, { timeout: 30_000 });
    await expect(
      page.getByRole("heading", { name: /Draft version \d+ is ready/i }),
    ).toBeVisible({ timeout: 60_000 });

    await expect(page.getByText(/could not be mapped/i)).toHaveCount(0);
  });
});
