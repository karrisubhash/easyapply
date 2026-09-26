import { E2E, OWNER_URL, STRANGER_URL } from "../support/env.cjs";
import { expect, test } from "../support/fixtures";
import { aConfirmedProfile, createJob, generateTailoredResume } from "../support/flows";

/**
 * The harness itself, and the boundary it exists to test.
 *
 * Two web servers with different `DEV_USER_EMAIL` values is the whole basis for
 * every ownership assertion in this suite, and it rests on a Next.js behaviour
 * — that `.env.local` does not overwrite a variable already in the environment
 * — which is documented but not ours. So it is asserted here rather than
 * assumed, and it runs first: if the two servers turned out to be the same
 * person, every ownership test below would pass for the wrong reason.
 */
test.describe("the two identities are genuinely two people", () => {
  test("each web server resolves to its own account", async ({ page, stranger }) => {
    // Touching the app provisions the identity just-in-time, so this also
    // proves the servers reach the API at all.
    await page.goto("/");
    await stranger.goto("/");

    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(stranger.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    // The shell prints who you are. Different servers, different people.
    await expect(page.getByTitle(E2E.ownerEmail)).toBeVisible();
    await expect(stranger.getByTitle(E2E.strangerEmail)).toBeVisible();

    expect(page.url()).toContain(String(E2E.ownerPort));
    expect(stranger.url()).toContain(String(E2E.strangerPort));
  });

  test("work done by one account is invisible to the other", async ({ page, stranger }) => {
    await aConfirmedProfile(page);
    const jobId = await createJob(page);

    // The owner sees their job.
    await page.goto("/jobs");
    await expect(page.getByRole("link", { name: /Backend Engineer/i }).first()).toBeVisible();

    // The stranger's listing is empty, and says so as an empty state rather
    // than as an error.
    await stranger.goto("/jobs");
    await expect(stranger.getByText("No jobs yet")).toBeVisible();
    await expect(stranger.getByRole("link", { name: /Backend Engineer/i })).toHaveCount(0);

    // And addressing it directly is a not-found, not a peek.
    await stranger.goto(`/jobs/${jobId}`);
    await expect(stranger.getByText("That job does not exist")).toBeVisible();
    await expect(stranger.getByText(/Acme Corp/)).toHaveCount(0);
  });

  test("a stranger cannot reach another account's tailored resume or its file", async ({
    page,
    stranger,
  }) => {
    await aConfirmedProfile(page);
    const jobId = await createJob(page);
    const runId = await generateTailoredResume(page, jobId);

    // The owner has an approved, downloadable document.
    await expect(page.getByRole("button", { name: "Download resume" })).toBeVisible();

    // The stranger, with the exact same URL, gets nothing.
    await stranger.goto(`/tailored-resumes/${runId}`);
    await expect(stranger.getByText("That tailored resume does not exist")).toBeVisible();
    await expect(stranger.getByRole("button", { name: /Download/i })).toHaveCount(0);
    await expect(stranger.getByText(/APTRANSCO/)).toHaveCount(0);

    // And the artifact endpoint itself, reached through the stranger's own
    // browser session rather than by a crafted request, hands over no bytes.
    const response = await stranger.request.get(
      `${STRANGER_URL}/api/backend/tailored-resumes/${runId}/artifact`,
    );
    expect(response.status()).toBe(404);
    const body = await response.body();
    expect(body.subarray(0, 2).toString()).not.toBe("PK");

    // The same URL on the owner's server does serve the document, which is what
    // makes the 404 above a statement about identity rather than about routing.
    const owned = await page.request.get(
      `${OWNER_URL}/api/backend/tailored-resumes/${runId}/artifact`,
    );
    expect(owned.status()).toBe(200);
    expect((await owned.body()).subarray(0, 2).toString()).toBe("PK");
  });
});
