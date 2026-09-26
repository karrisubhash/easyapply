import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";

import { API_DIR, E2E, OWNER_URL, PYTHON } from "../support/env.cjs";
import { expect, test } from "../support/fixtures";
import { confirmVersion, createProfile } from "../support/flows";

/**
 * The product, end to end, with the documents it was actually built for.
 *
 * `journey.spec.ts` already walks the seams — that what one step produced is
 * named by the next. It walks them with a two-line fixture PDF and a job
 * description typed to suit it, which is the right trade for a test about
 * CONNECTIONS.
 *
 * This one is about CONTENT. It uploads the evaluation dataset's resume — the
 * document with a contact block, a summary, labelled skill rows, wrapped
 * bullets, two projects, certifications and real PDF hyperlink annotations —
 * and aims it at the real posting from `matching_real_v1` that the tailoring
 * work was measured against. Then it reads the downloaded .docx back with the
 * library that wrote it and asserts what a recruiter would see.
 *
 * The distinction matters because every defect the tailoring review found was
 * invisible to a green suite: the document reached `completed`, downloaded
 * cleanly, and had no name on it.
 */

/**
 * `j_backend_senior`, verbatim from
 * `services/api/evaluation/datasets/matching_real_v1/jobs.json`.
 *
 * Copied rather than imported: this is a browser suite and that file is Python
 * territory, and a posting is input data that should read as input data in the
 * test that uses it. It is the same text the tailoring evaluation runs against,
 * so a disagreement between this suite and that dataset is a real disagreement
 * rather than two different postings.
 */
const REAL_JOB_DESCRIPTION = `We are looking for a Senior Backend Engineer to join our platform team in Berlin.

What we need:
- Strong Python, with 5+ years of professional software engineering experience
- 3+ years building production services with Django
- Solid PostgreSQL: you should be comfortable designing schemas and reading a query plan
- Comfortable with Docker
- Bachelor's degree in Computer Science or a related field
- Must be legally authorised to work in the EU
- Based in Berlin, or within two hours of CET for our hybrid days

Nice to have:
- Kubernetes
- Redis
- AWS Certified Developer
`;

const REAL_JOB_TITLE = "Senior Backend Engineer";
const REAL_JOB_COMPANY = "Northbound Logistics GmbH";

/** Where the run leaves its screenshots, beside the suite's other artifacts. */
const SHOTS = resolve(E2E.downloadDir, "../flow-screenshots");

/** Read a downloaded .docx back with `python-docx`. See `docx_text.py`. */
function readDocx(path: string): {
  paragraphs: string[];
  text: string;
  runs: { text: string; bold: boolean; size: number | null }[];
  tables: string[][];
} {
  const output = execFileSync(
    PYTHON,
    [resolve(API_DIR, "../../apps/web/e2e/support/docx_text.py"), path],
    { cwd: API_DIR, stdio: "pipe" },
  );
  return JSON.parse(output.toString());
}

test.describe("the real product flow", () => {
  // Three real workers, a real browser and a real document. The default 90s is
  // for a two-line PDF.
  test.setTimeout(180_000);

  test("a real resume becomes a real tailored .docx, and every step shows its work", async ({
    page,
  }) => {
    // --- 1. the dashboard ---------------------------------------------------
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("You do not have a profile yet.")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/flow-01-dashboard.png`, fullPage: true });

    await createProfile(page);

    // --- 2-3. upload the real resume and wait for the worker ----------------
    await page.goto("/resume");
    await page.getByLabel("Resume file").setInputFiles(E2E.goldenResume);
    await expect(page).toHaveURL(/\/resume\/\d+$/, { timeout: 30_000 });

    const ready = page.getByRole("heading", { name: /Draft version \d+ is ready/i });
    await expect(ready).toBeVisible({ timeout: 90_000 });
    const versionNumber = Number(
      (await ready.textContent())?.match(/Draft version (\d+) is ready/i)?.[1],
    );
    expect(versionNumber).toBeGreaterThan(0);

    // Nothing the schema could not hold. An entry here is data the document
    // stated and the profile lost.
    await expect(page.getByText(/could not be mapped/i)).toHaveCount(0);

    // --- 4-5. the profile, as a person reads it -----------------------------
    await page.goto(`/profile/versions/${versionNumber}`);

    // name and contact
    await expect(page.getByText("ALEX MORGAN")).toBeVisible();
    await expect(page.getByText(/alex\.morgan@example\.com/)).toBeVisible();
    await expect(page.getByText(/\+44 7700 900123/)).toBeVisible();
    await expect(page.getByText(/Bristol, United Kingdom/).first()).toBeVisible();

    // summary, in the candidate's own words
    await expect(
      page.getByText(/Backend engineer focused on distributed systems/),
    ).toBeVisible();

    // links — these live in the PDF's annotations, not its text
    await expect(page.getByRole("link", { name: "LinkedIn" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/alex-morgan-example/",
    );
    await expect(page.getByRole("link", { name: "Portfolio" })).toHaveAttribute(
      "href",
      "https://alexmorgan.example.com",
    );

    // experience, both roles, with the wrapped bullet reassembled
    await expect(page.getByText("Senior Backend Engineer")).toBeVisible();
    await expect(page.getByText("Northwind Logistics")).toBeVisible();
    await expect(page.getByText("Cavendish Health")).toBeVisible();
    await expect(
      page.getByText(/cutting p99 latency by 63% across 4M daily events/),
    ).toBeVisible();

    // project bullets and technologies
    await expect(page.getByText("Ledger Reconciliation Engine")).toBeVisible();
    await expect(
      page.getByText(/Reconciles 1\.2M daily transactions against three upstream ledgers/),
    ).toBeVisible();
    await expect(page.getByText("Celery, PostgreSQL, Python")).toBeVisible();

    // education and skills
    await expect(page.getByText(/University of Bristol/)).toBeVisible();
    await expect(page.getByText(/First Class Honours/)).toBeVisible();
    await expect(page.getByText("Python", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Load Testing", { exact: true })).toBeVisible();
    // The label was glued onto the first skill of its row before the fix.
    await expect(page.getByText("Languages:Python")).toHaveCount(0);

    await page.screenshot({ path: `${SHOTS}/flow-02-profile.png`, fullPage: true });

    // --- 6. confirm ---------------------------------------------------------
    await confirmVersion(page, versionNumber);
    await expect(page.getByText("This version is confirmed")).toBeVisible();

    // --- 7-8. a real posting, and the requirements read out of it -----------
    await page.goto("/jobs/new");
    await page.getByLabel(/^Job title/).fill(REAL_JOB_TITLE);
    await page.getByLabel(/^Company/).fill(REAL_JOB_COMPANY);
    await page.getByLabel(/^Posting URL/).fill("https://example.com/jobs/senior-backend");
    await page.getByLabel(/^Description/).fill(REAL_JOB_DESCRIPTION);
    await page.getByRole("button", { name: "Save job" }).click();

    await expect(page).toHaveURL(/\/jobs\/\d+$/, { timeout: 30_000 });
    const jobId = Number(page.url().match(/\/jobs\/(\d+)$/)![1]);

    await page.goto(`/jobs/${jobId}`);
    // Structured requirements, not a copy of the posting. The description is
    // rendered too, so each of these is asserted where the requirements are.
    await expect(page.getByRole("heading", { name: /^Technologies/ })).toBeVisible();
    for (const technology of ["Python", "PostgreSQL", "Docker", "Kubernetes"]) {
      await expect(page.getByText(technology, { exact: true }).first()).toBeVisible();
    }

    await page.screenshot({ path: `${SHOTS}/flow-03-job.png`, fullPage: true });

    // --- 9-10. matching -----------------------------------------------------
    await page.goto(`/jobs/${jobId}/match`);
    await page.getByRole("button", { name: /Match my profile/i }).click();
    await expect(page.getByText("Requirement coverage")).toBeVisible({ timeout: 60_000 });

    // Requirement-LEVEL results, not just a score.
    await expect(page.getByText("Matched").first()).toBeVisible();

    // The verdict names the exact inputs it is reproducible from, and the
    // version among them is the one just confirmed.
    await expect(
      page.getByText(new RegExp(`Profile version ${versionNumber}`)),
    ).toBeVisible();

    // Every required technology the posting states has a verdict of its own.
    // Before the structuring fix, this page showed three OPTIONAL technologies
    // and nothing else — the posting's entire required section had been dropped
    // and the screen still looked healthy.
    for (const technology of ["Python", "Django", "PostgreSQL", "Docker"]) {
      await expect(page.getByText(technology, { exact: true }).first()).toBeVisible();
    }

    // Evidence is SHOWN, not merely counted. A coverage figure with nothing
    // under it is a score a person has no way to check.
    expect(await page.getByTestId("evidence").count()).toBeGreaterThan(0);

    // Evidence in the user's own words rather than a row id. A provenance
    // display that says "experience #101" is asking a person to trust a number
    // they have no way to look up.
    const matchBody = await page.locator("main").innerText();
    expect(matchBody).not.toMatch(/\bsource_id\b/i);
    expect(matchBody).not.toMatch(/\b(?:experience|project|education|certification)_id\b/i);
    expect(matchBody).not.toMatch(/\b(?:experience|project|skill) #\d+/i);
    // No bare primary key standing in for a name.
    expect(matchBody).not.toMatch(/\bid[:=]\s*\d+/i);

    await page.screenshot({ path: `${SHOTS}/flow-04-match.png`, fullPage: true });

    // --- 11-12. the tailoring page, which states its input first ------------
    await page.goto(`/jobs/${jobId}/tailor`);
    await expect(page.getByText(new RegExp(`version ${versionNumber}`))).toBeVisible();

    // --- 13-14. generate and wait -------------------------------------------
    await page.getByRole("button", { name: "Generate resume" }).click();

    if (!/\/tailored-resumes\/\d+/.test(page.url())) {
      const openLast = page.getByRole("link", { name: /Open the last one/i });
      await expect(openLast).toBeVisible({ timeout: 90_000 });
      await openLast.click();
    }
    await expect(page).toHaveURL(/\/tailored-resumes\/\d+$/, { timeout: 30_000 });
    const runId = Number(/\/tailored-resumes\/(\d+)$/.exec(page.url())![1]);

    // --- 15. the grounding verdict, on screen -------------------------------
    await expect(
      page.getByText(/Every statement traces back to your profile/i),
    ).toBeVisible({ timeout: 90_000 });

    // --- 16. the document, and that it was built from the confirmed version --
    await expect(
      page.getByRole("link", { name: `Version ${versionNumber}` }),
    ).toHaveAttribute("href", `/profile/versions/${versionNumber}`);

    const reviewBody = await page.locator("main").innerText();
    // Selected the evidence this posting asks for...
    expect(reviewBody).toContain("Python");
    expect(reviewBody).toContain("PostgreSQL");
    // ...and the candidate's own summary rather than a repeat of a bullet.
    expect(reviewBody).toContain("Backend engineer focused on distributed systems");

    await page.screenshot({ path: `${SHOTS}/flow-05-tailored.png`, fullPage: true });

    // --- 17. download -------------------------------------------------------
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download resume" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.docx$/);
    const file = (await download.path())!;
    expect(readFileSync(file).subarray(0, 2).toString()).toBe("PK");
    expect(statSync(file).size).toBeGreaterThan(1000);

    // --- 18. and what is actually in it -------------------------------------
    const docx = readDocx(file);
    const text = docx.text;

    // It opens in a real reader, and has content.
    expect(docx.paragraphs.length).toBeGreaterThan(10);

    // Who it is from. The defect that started the tailoring review was a
    // flawlessly grounded document with none of this on it.
    expect(text).toContain("ALEX MORGAN");
    expect(text).toContain("alex.morgan@example.com");
    expect(text).toContain("+44 7700 900123");
    expect(text).toContain("Bristol, United Kingdom");
    // Links print as their addresses, not their captions: on paper, or to an
    // ATS reading text, "LinkedIn" alone tells a reader nothing they can use.
    expect(text).toContain("linkedin.com/in/alex-morgan-example");
    expect(text).toContain("github.com/alexmorgan-example");

    // The name is formatted as a name, not merely present somewhere.
    const nameRun = docx.runs.find((run) => run.text.includes("ALEX MORGAN"));
    expect(nameRun?.bold).toBe(true);
    expect(nameRun?.size ?? 0).toBeGreaterThanOrEqual(14);

    // Summary, in the candidate's own words.
    expect(text).toContain("Backend engineer focused on distributed systems");

    // The skills this posting named, in the POSTING's order, ahead of the ones
    // it did not name.
    //
    // This line is also the end-to-end proof of the job-structuring fix. The
    // posting is headed "What we need:", which the structurer did not
    // recognize, so it dropped Python, Django, PostgreSQL and Docker and kept
    // only the "Nice to have:" items. This document was therefore led by
    // Kubernetes, Redis and AWS — a Python engineer's resume, aimed at a Python
    // role, leading with three technologies the posting called optional.
    expect(text).toMatch(
      /Relevant to this role:\s*Python,\s*Django,\s*PostgreSQL,\s*Docker/,
    );

    // Relevant experience and project evidence, with their metrics intact.
    expect(text).toContain("Northwind Logistics");
    expect(text).toContain("Cavendish Health");
    expect(text).toMatch(/cutting p99 latency by 63%/);
    expect(text).toContain("Ledger Reconciliation Engine");

    // Education, at the precision the resume stated it.
    expect(text).toMatch(/University of Bristol/);
    expect(text).toContain("Computer Science");
    expect(text).toMatch(/2015\s*[–-]\s*2018/);
    expect(text).not.toContain("Jan 2015");

    // None of our plumbing on a page sent to an employer.
    expect(text).not.toContain("Generated by EasyApply");
    expect(text).not.toContain("Prepared for");
    expect(text).not.toMatch(/confirmed profile version/i);

    // Nothing the profile does not support. Each of these is a technology the
    // posting asks for and the candidate's document must not pretend to have
    // used in prose it never wrote.
    expect(text).not.toMatch(/\bReact\b/);
    expect(text).not.toMatch(/\bJavaScript\b/);

    // No broken formatting: no empty-looking paragraphs where text should be,
    // no layout tables an applicant tracking system would mangle.
    expect(docx.tables).toHaveLength(0);
    expect(docx.paragraphs.every((line) => line.trim().length > 0)).toBe(true);

    // --- and the same bytes through the proxy, with the API's own headers ---
    const proxied = await page.request.get(
      `${OWNER_URL}/api/backend/tailored-resumes/${runId}/artifact`,
    );
    expect(proxied.status()).toBe(200);
    expect(proxied.headers()["content-type"]).toContain("wordprocessingml.document");
    expect((await proxied.body()).subarray(0, 2).toString()).toBe("PK");
  });
});
