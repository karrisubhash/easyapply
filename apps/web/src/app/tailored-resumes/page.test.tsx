import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import TailoredResumesPage from "./page";
import { render, screen, within } from "@/test/render";
import { apiError, server } from "@/test/server";
import { tailoringRun } from "@/test/tailoring-fixtures";

const BASE = "/api/backend";

function list(runs: unknown[]) {
  server.use(http.get(`${BASE}/tailored-resumes`, () => HttpResponse.json(runs)));
}

describe("TailoredResumesPage", () => {
  it("says it is loading rather than showing a blank page", () => {
    render(<TailoredResumesPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading tailored resumes");
    // The dangerous flash is the wrong one: "you have none" before anything has
    // arrived is an invented fact about the user's account.
    expect(screen.queryByText("Nothing generated yet")).not.toBeInTheDocument();
  });

  it("tells a user with no resumes where they come from", async () => {
    list([]);

    render(<TailoredResumesPage />);

    expect(await screen.findByText("Nothing generated yet")).toBeInTheDocument();
    // An empty listing with no way out is a dead end. The route has to be one
    // that exists, because tailoring starts from a job and nowhere else.
    expect(screen.getByRole("link", { name: "Go to jobs" })).toHaveAttribute("href", "/jobs");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // --- what makes one row different from another -------------------------------

  it("names the profile version each run was built from, not just the job", async () => {
    // The case the listing exists to survive: the same job, tailored twice,
    // after the user confirmed a new profile version. These are two different
    // documents, and a row that showed only the job title would render them as
    // two identical lines.
    list([
      tailoringRun({ id: 12, profile_version_id: 104, profile_version_number: 3 }),
      tailoringRun({ id: 9, profile_version_id: 102, profile_version_number: 1 }),
    ]);

    render(<TailoredResumesPage />);

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(2);

    for (const row of rows) {
      expect(row).toHaveTextContent("Senior Backend Engineer");
      expect(row).toHaveTextContent("Acme Corp");
    }
    expect(rows[0]).toHaveTextContent("From profile version 3");
    expect(rows[1]).toHaveTextContent("From profile version 1");
  });

  it("distinguishes two attempts at the same job from the same version", async () => {
    // Version alone is not enough when a run was retried: both documents came
    // from version 1 of the same profile, and only the attempt separates them.
    list([
      tailoringRun({ id: 12, attempt: 2 }),
      tailoringRun({ id: 9, attempt: 1 }),
    ]);

    render(<TailoredResumesPage />);

    const rows = await screen.findAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Attempt 2");
    // A first attempt is the normal case, so saying so is noise on every row.
    expect(rows[1]).not.toHaveTextContent(/Attempt/);
  });

  it("sends each row to its own run and nowhere else", async () => {
    list([tailoringRun({ id: 12 }), tailoringRun({ id: 9 })]);

    render(<TailoredResumesPage />);

    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[0]).getByRole("link", { name: /^Review / })).toHaveAttribute(
      "href",
      "/tailored-resumes/12",
    );
    expect(within(rows[1]).getByRole("link", { name: /^Review / })).toHaveAttribute(
      "href",
      "/tailored-resumes/9",
    );
  });

  it("gives every row's link a name of its own, attempts included", async () => {
    // Two documents for one job, which is the case a job title alone cannot
    // separate — and the case the visible row already separates by attempt.
    // Attempts are counted per job AND version, so the third row is "attempt
    // 2" too — of a different document.
    list([
      tailoringRun({ id: 12, attempt: 2 }),
      tailoringRun({ id: 9, attempt: 1 }),
      tailoringRun({ id: 15, attempt: 2, profile_version_id: 205, profile_version_number: 2 }),
    ]);

    render(<TailoredResumesPage />);

    const links = await screen.findAllByRole("link", { name: /^Review / });
    const names = links.map((link) => link.getAttribute("aria-label"));
    expect(names).toEqual([
      "Review Senior Backend Engineer at Acme Corp, from version 1, attempt 2",
      "Review Senior Backend Engineer at Acme Corp, from version 1",
      "Review Senior Backend Engineer at Acme Corp, from version 2, attempt 2",
    ]);
    expect(document.title).toBe("Tailored resumes · EasyApply");
    expect(new Set(names).size).toBe(names.length);
  });

  it("dates every run without rendering a broken date", async () => {
    list([tailoringRun()]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    // Not asserted to the minute: formatDateTime renders in the reader's own
    // locale and zone, and pinning either would test the test runner's config.
    expect(within(row).getByText(/^Generated \S/)).toBeInTheDocument();
    expect(row.textContent).not.toMatch(/Invalid Date|NaN|undefined/);
  });

  // --- §9: a document we do not stand behind must not look like one we do -------

  it("labels a run whose grounding failed differently from one that passed", async () => {
    list([
      tailoringRun({ id: 12, grounding_status: "passed", has_artifact: true }),
      tailoringRun({
        id: 9,
        status: "completed",
        grounding_status: "failed",
        error_count: 1,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[0]).getByText("Ready")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Not approved — no file")).toBeInTheDocument();
  });

  it("never calls a failed document ready or complete", async () => {
    // Both runs finished, so status alone says "completed" for each. The word
    // the user reads must come from the grounding verdict instead: a document
    // carrying a claim the profile does not support has no file behind it, and
    // anything implying otherwise sends someone to send it.
    list([
      tailoringRun({
        id: 9,
        status: "completed",
        grounding_status: "failed",
        error_count: 1,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    expect(within(row).getByText("Not approved — no file")).toBeInTheDocument();
    expect(row.textContent).not.toMatch(/\bready\b|\bcomplete/i);
  });

  it("offers a failed run no shortcut past the review page", async () => {
    // §9: the artifact does not exist for this run. The listing must not grow a
    // download that would 409, and Review is the only place the user can learn
    // which sentence was the problem.
    list([
      tailoringRun({
        id: 9,
        status: "completed",
        grounding_status: "failed",
        error_count: 1,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    expect(within(row).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Review",
    ]);
    expect(within(row).queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("marks a run that failed outright as failed, not as needing review", async () => {
    // Nothing was written at all here, so "Not approved — no file" would point the user
    // at a document that does not exist.
    list([
      tailoringRun({
        id: 9,
        status: "failed",
        failed_stage: "generate",
        error_code: "ai_rate_limited",
        error_message: "The writing service was busy.",
        grounding_status: null,
        error_count: null,
        warning_count: null,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    expect(within(row).getByText("Failed")).toBeInTheDocument();
    expect(within(row).queryByText("Not approved — no file")).not.toBeInTheDocument();
  });

  it("names the stage a run is still at instead of calling it ready", async () => {
    list([
      tailoringRun({
        id: 9,
        status: "validating",
        is_terminal: false,
        finished_at: null,
        grounding_status: null,
        error_count: null,
        warning_count: null,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    expect(within(row).getByText("Checking against your profile")).toBeInTheDocument();
    expect(row.textContent).not.toMatch(/\bready\b/i);
  });

  it("says a run has stopped responding rather than showing it working forever", async () => {
    // The listing polls while anything is active, so a run the worker dropped
    // would otherwise sit at "Writing" for as long as the tab is open.
    list([
      tailoringRun({
        id: 9,
        status: "generating",
        is_terminal: false,
        is_stalled: true,
        finished_at: null,
        grounding_status: null,
        error_count: null,
        warning_count: null,
        has_artifact: false,
      }),
    ]);

    render(<TailoredResumesPage />);

    const row = await screen.findByRole("listitem");
    expect(within(row).getByText("Stopped responding")).toBeInTheDocument();
    expect(within(row).queryByText("Writing")).not.toBeInTheDocument();
  });

  // --- the row still has to be readable when the join came back thin ------------

  it("identifies a run whose job could not be named, without printing undefined", async () => {
    list([
      tailoringRun({
        id: 5,
        job_title: null,
        company_name: null,
        profile_version_number: null,
      }),
    ]);

    render(<TailoredResumesPage />);

    expect(await screen.findByText("Untitled job")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review untitled job" })).toBeInTheDocument();
    const row = screen.getByRole("listitem");
    expect(row.textContent).not.toMatch(/undefined|\bnull\b|\[object/i);
  });

  // --- failure -------------------------------------------------------------------

  it("shows the API's own sentence when the list cannot be read", async () => {
    // A 409 rather than a 500 because the query layer deliberately does not
    // retry one — see `retryUnlessExpected`. A 500 would be three attempts and
    // two backoffs before anything rendered.
    server.use(
      http.get(`${BASE}/tailored-resumes`, () =>
        apiError(409, "Your resumes are being migrated and cannot be listed yet.", "conflict"),
      ),
    );

    render(<TailoredResumesPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/being migrated and cannot be listed yet/i);
    // No stack trace, no SQL, no storage key, no driver name.
    expect(document.body.textContent).not.toMatch(
      /Traceback|SELECT |psycopg|storage_key|tailored\/\d/,
    );
  });

  it("does not claim the user has no resumes when the request failed", async () => {
    // A failed read and an empty account are different facts. Showing the empty
    // state for the first is the invented statistic rule in miniature — it tells
    // someone with twelve resumes that they have none.
    server.use(
      http.get(`${BASE}/tailored-resumes`, () =>
        apiError(404, "That listing is not available.", "not_found"),
      ),
    );

    render(<TailoredResumesPage />);

    await screen.findByRole("alert");
    expect(screen.queryByText("Nothing generated yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to jobs" })).not.toBeInTheDocument();
    // And no permanent spinner left behind the alert.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
