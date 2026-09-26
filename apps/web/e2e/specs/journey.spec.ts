import { OWNER_URL } from "../support/env.cjs";
import { expect, test } from "../support/fixtures";
import {
  COMPANY,
  IRRELEVANT_SENTENCE,
  JOB_TITLE,
  RELEVANT_SENTENCE,
  aConfirmedProfile,
  createJob,
} from "../support/flows";

/**
 * The eleven steps a person actually takes, in one browser, in order.
 *
 * The other specs in this directory each prove one property deeply: that two
 * identities are two people, that a real document survives the pipeline onto
 * the screen. Neither asserts that the steps CONNECT — that the version
 * confirmed in step 6 is the version the document in step 10 was built from,
 * that the file offered in step 11 is the file the API rendered. A product can
 * pass every one of its parts and still have a seam nobody walks across.
 *
 * So this runs the journey whole, and the assertions sit at the seams: what one
 * step produced is named by the next.
 */
test.describe("the whole journey", () => {
  test("dashboard to downloaded document, without leaving the browser", async ({ page }) => {
    // --- 1. the dashboard, on a completely empty account --------------------
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByText("You do not have a profile yet.")).toBeVisible();
    // An empty collection says it is empty and offers the action that fills it.
    // "Empty" and "not loaded yet" are different facts and must read differently.
    await expect(page.getByText("No resumes uploaded yet.")).toBeVisible();
    await expect(page.getByText("No jobs saved yet.")).toBeVisible();

    // --- 2-6. resume in, draft out, draft confirmed -------------------------
    // The steps themselves live in `flows.ts`; what matters here is the number
    // they return, because every later assertion is pinned to it.
    const versionNumber = await aConfirmedProfile(page);
    expect(versionNumber).toBeGreaterThan(0);

    await page.goto(`/profile/versions/${versionNumber}`);
    await expect(page.getByText("This version is confirmed")).toBeVisible();
    // The sentence tailoring will later copy is in the version, in these words.
    await expect(page.getByText(RELEVANT_SENTENCE)).toBeVisible();

    // The dashboard now reflects the work rather than the empty state it opened on.
    await page.goto("/");
    await expect(page.getByText(/^Confirmed /)).toBeVisible();
    await expect(page.getByText("No resumes uploaded yet.")).toHaveCount(0);

    // --- 7-8. a job, and the requirements read out of it --------------------
    const jobId = await createJob(page);

    await page.goto(`/jobs/${jobId}`);
    // Requirements, not a copy of the posting: these words appear in the
    // structured output because something parsed the description, and a job
    // whose structuring silently produced nothing would still render the
    // description above them.
    await expect(page.getByRole("heading", { name: /^Technologies/ })).toBeVisible();
    await expect(page.getByText("Python", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("PostgreSQL", { exact: true }).first()).toBeVisible();

    // --- 9. matching, evidence first ---------------------------------------
    await page.goto(`/jobs/${jobId}/match`);
    await page.getByRole("button", { name: /Match my profile/i }).click();
    await expect(page.getByText("Requirement coverage")).toBeVisible({ timeout: 45_000 });

    // A verdict is not a number: the footer names the exact inputs the result
    // is reproducible from, and the version among them is the one just confirmed.
    await expect(page.getByText("Matched").first()).toBeVisible();
    await expect(
      page.getByText(new RegExp(`Profile version ${versionNumber}`)),
    ).toBeVisible();

    // --- 10. generation, and the grounding verdict --------------------------
    await page.goto(`/jobs/${jobId}/tailor`);
    // The page states which version it will build from BEFORE it builds
    // anything — a document whose input the user learns about afterwards is one
    // they cannot check in advance.
    await expect(page.getByText(new RegExp(`version ${versionNumber}`))).toBeVisible();
    await page.getByRole("button", { name: "Generate resume" }).click();

    if (!/\/tailored-resumes\/\d+/.test(page.url())) {
      const openLast = page.getByRole("link", { name: /Open the last one/i });
      await expect(openLast).toBeVisible({ timeout: 60_000 });
      await openLast.click();
    }
    await expect(page).toHaveURL(/\/tailored-resumes\/\d+$/, { timeout: 30_000 });
    const runId = Number(/\/tailored-resumes\/(\d+)$/.exec(page.url())![1]);

    await expect(
      page.getByText(/Every statement traces back to your profile/i),
    ).toBeVisible({ timeout: 60_000 });

    // The seam: this document was built from the version confirmed in step 6,
    // and the page says so with a link back to it rather than asking anyone to
    // take it on trust.
    await expect(
      page.getByRole("link", { name: `Version ${versionNumber}` }),
    ).toHaveAttribute("href", `/profile/versions/${versionNumber}`);

    // The generator SELECTS; it does not write. Every line is a clause copied
    // out of the confirmed version and the page marks each one with the text it
    // came from — including the sentence this posting never asked for, which is
    // carried because the profile said it, not because it was relevant. A line
    // that could not be marked is what the grounding check withholds the file
    // over, so the marking is the property worth asserting here.
    await expect(page.getByText(RELEVANT_SENTENCE).first()).toBeVisible();
    await expect(
      page.getByRole("listitem").filter({ hasText: IRRELEVANT_SENTENCE }).first(),
    ).toContainText("copied from your profile");
    await expect(page.getByText(COMPANY).first()).toBeVisible();
    await expect(page.getByText(JOB_TITLE).first()).toBeVisible();

    // --- 11. the file ------------------------------------------------------
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download resume" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.docx$/);

    const { readFileSync, statSync } = await import("node:fs");
    const file = (await download.path())!;
    // A DOCX is a zip. Asserting the magic bytes is how this test notices the
    // proxy handing back a JSON error body, or a UTF-8 round trip through
    // `text()` corrupting the archive — both of which "download successfully".
    expect(readFileSync(file).subarray(0, 2).toString()).toBe("PK");
    expect(statSync(file).size).toBeGreaterThan(1000);

    // --- and the same bytes through the proxy, with the API's own headers ---
    // The filename and the media type are decided server-side from the artifact
    // row; the proxy forwards them rather than re-deriving them, so this asserts
    // they survive the hop at all.
    const proxied = await page.request.get(
      `${OWNER_URL}/api/backend/tailored-resumes/${runId}/artifact`,
    );
    expect(proxied.status()).toBe(200);
    expect(proxied.headers()["content-type"]).toContain("wordprocessingml.document");
    expect(proxied.headers()["content-disposition"]).toContain(".docx");
    expect((await proxied.body()).subarray(0, 2).toString()).toBe("PK");
  });

  test("the page is usable again after a generation that finished at once", async ({
    page,
  }) => {
    /**
     * The deterministic generator is fast — fast enough that a run is regularly
     * terminal before anything refetches it. The tailor page then had no live
     * source for the run it had just started: the detail poll is enabled only
     * while the LISTING says a run is active, so it never ran, and the page fell
     * back to the POST's own response, frozen at QUEUED. It reported "Queued…"
     * under a disabled Generate button for the rest of the session, beside a
     * list showing the finished document. Only a reload cleared it.
     *
     * This needs a real worker winning a real race, which is why it is asserted
     * here rather than in the unit suite.
     */
    await aConfirmedProfile(page);
    const jobId = await createJob(page);

    await page.goto(`/jobs/${jobId}/tailor`);
    await page.getByRole("button", { name: "Generate resume" }).click();

    // The run finishes and the listing catches up. What must come back is the
    // button: the page's only way to generate again.
    await expect(page.getByRole("link", { name: /Open the last one/i })).toBeVisible({
      timeout: 60_000,
    });
    const generate = page.getByRole("button", { name: "Generate resume" });
    await expect(generate).toBeEnabled({ timeout: 30_000 });

    // And nothing on the page still claims work is happening.
    await expect(page.getByText("Queued…")).toHaveCount(0);
    await expect(
      page.getByText(/this page updates itself while the work runs/i),
    ).toHaveCount(0);

    // Not merely enabled — usable. A second document is generated without the
    // reload the bug forced.
    await generate.click();
    // The row in Previous attempts, not the in-flight panel's "Attempt 2." —
    // the panel is transient and both would match a bare text query.
    await expect(page.getByRole("link", { name: /^Attempt 2 / })).toBeVisible({
      timeout: 60_000,
    });
  });

  test("a refresh and the back button keep their place", async ({ page }) => {
    await aConfirmedProfile(page);
    const jobId = await createJob(page);

    // Every page here is client-rendered against the proxy, so a reload is a
    // genuinely different path from a client-side navigation — and it is the one
    // a person uses when they think something is stuck.
    await page.goto(`/jobs/${jobId}`);
    await expect(page.getByRole("heading", { name: /Backend Engineer/ })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: /Backend Engineer/ })).toBeVisible();
    await expect(page.getByText("Python", { exact: true }).first()).toBeVisible();

    // Back and forward across the journey, each landing on the page it names
    // rather than on a shell that never refetched.
    await page.goto("/");
    await page.getByRole("navigation").getByRole("link", { name: "Jobs" }).click();
    await expect(page).toHaveURL(/\/jobs$/);
    await page.getByRole("link", { name: /^View / }).first().click();
    await expect(page).toHaveURL(new RegExp(`/jobs/${jobId}$`));

    await page.goBack();
    await expect(page).toHaveURL(/\/jobs$/);
    await expect(page.getByRole("heading", { name: "Jobs", exact: true })).toBeVisible();

    await page.goBack();
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    await page.goForward();
    await page.goForward();
    await expect(page).toHaveURL(new RegExp(`/jobs/${jobId}$`));
    // Forward-navigated, not re-entered: the data is there, not a spinner.
    await expect(page.getByRole("heading", { name: /Backend Engineer/ })).toBeVisible();
  });

  test("pressing Save job twice saves one job", async ({ page }) => {
    await aConfirmedProfile(page);

    await page.goto("/jobs/new");
    await page.getByLabel(/^Job title/).fill("Platform Engineer");
    await page.getByLabel(/^Company/).fill("Globex");
    await page.getByLabel(/^Posting URL/).fill("https://example.com/jobs/platform");
    await page.getByLabel(/^Description/).fill("Requirements:\n- Python\n- Docker\n");

    const save = page.getByRole("button", { name: "Save job" });
    // Two clicks as fast as the browser will dispatch them. The second either
    // lands on a disabled button or after the navigation has begun; neither may
    // produce a second job. `noWaitAfter` so the second click does not wait on a
    // navigation the first already started.
    await save.click({ noWaitAfter: true });
    await save.click({ noWaitAfter: true, force: true }).catch(() => {});

    await expect(page).toHaveURL(/\/jobs\/\d+$/, { timeout: 30_000 });

    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: "View Platform Engineer at Globex" })).toHaveCount(1);
  });
});
