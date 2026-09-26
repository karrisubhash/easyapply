import { http, HttpResponse } from "msw";
import { describe, expect, it, vi, beforeEach } from "vitest";

import TailoredResumePage from "./page";
import type { TailoringRunDetail } from "@easyapply/shared";

import { render, screen, waitFor, within } from "@/test/render";
import { apiError, server } from "@/test/server";
import {
  tailoredDocument,
  tailoredResume,
  tailoringIssue,
  tailoringRunDetail,
  tailoringRunInFlight,
} from "@/test/tailoring-fixtures";

vi.mock("next/navigation", () => ({
  useParams: () => ({ runId: "1" }),
}));

const BASE = "/api/backend";

function detail(body: TailoringRunDetail | Record<string, unknown>, status = 200) {
  server.use(
    http.get(`${BASE}/tailored-resumes/:id`, () => HttpResponse.json(body, { status })),
  );
}

describe("TailoredResumePage", () => {
  it("names the profile version the resume was built from", async () => {
    render(<TailoredResumePage />);

    // §8: the user must know exactly which profile was used. It is on the page
    // before the document, not behind a disclosure.
    expect(await screen.findByText("Profile version")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Version 1" })).toHaveAttribute(
      "href",
      "/profile/versions/1",
    );
  });

  it("says a deterministic document is the user's own wording, not a model's", async () => {
    render(<TailoredResumePage />);

    expect(
      await screen.findByText(/selecting your own wording/i),
    ).toBeInTheDocument();
  });

  it("names the model when one wrote it", async () => {
    detail(tailoringRunDetail({ generator: "ai", model: "claude-opus-5" }));

    render(<TailoredResumePage />);

    expect(await screen.findByText(/claude-opus-5/)).toBeInTheDocument();
  });

  it("shows the grounding verdict above the document", async () => {
    render(<TailoredResumePage />);

    expect(
      await screen.findByText(/every statement traces back to your profile/i),
    ).toBeInTheDocument();
  });

  it("offers the download for an approved document", async () => {
    render(<TailoredResumePage />);

    expect(
      await screen.findByRole("button", { name: /download resume/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Acme-Corp-Senior-Backend-Engineer-v1\.docx/),
    ).toBeInTheDocument();
  });

  // --- the rule the whole feature exists for -----------------------------------

  it("refuses the download when the document failed grounding, and says why", async () => {
    detail(
      tailoringRunDetail({
        result: tailoredResume({ issues: [tailoringIssue()] }),
      }),
    );

    render(<TailoredResumePage />);

    expect(await screen.findByText(/was not approved/i)).toBeInTheDocument();
    expect(
      screen.getByText(/states a number the text it cites does not contain/i),
    ).toBeInTheDocument();
    // The claim itself, so the user can find the sentence in the document.
    expect(screen.getByText(/40 engineers/)).toBeInTheDocument();
    // Absent, not disabled: there is no "yet" here — a rejected document will
    // never have a file.
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("still shows a rejected document so the user can see what was written", async () => {
    detail(
      tailoringRunDetail({
        result: tailoredResume({ issues: [tailoringIssue()] }),
      }),
    );

    render(<TailoredResumePage />);

    expect(await screen.findByText("Experience")).toBeInTheDocument();
    expect(
      screen.getByText("Senior Backend Engineer, Acme Corp"),
    ).toBeInTheDocument();
  });

  it("does not call a rejected document a draft or merely unverified", async () => {
    detail(
      tailoringRunDetail({
        result: tailoredResume({ issues: [tailoringIssue()] }),
      }),
    );

    render(<TailoredResumePage />);
    await screen.findByText(/was not approved/i);

    // Those words suggest nobody has looked yet. Something did look, and it
    // found a specific sentence.
    expect(screen.queryByText(/\bunverified\b/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pending review/i)).not.toBeInTheDocument();
  });

  it("shows warnings without withholding the file", async () => {
    detail(
      tailoringRunDetail({
        result: tailoredResume({
          issues: [
            tailoringIssue({
              severity: "warning",
              reason: "This wording is not in your profile.",
              claim: "Delivered the reporting pipeline migration onto PostgreSQL.",
            }),
          ],
        }),
      }),
    );

    render(<TailoredResumePage />);

    expect(await screen.findByText(/worth a second look/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download resume/i })).toBeInTheDocument();
  });

  // --- ORIGINAL -> TAILORED ------------------------------------------------------

  it("shows the profile text behind a line whose wording changed", async () => {
    render(<TailoredResumePage />);

    // The generated text appears twice inside the disclosure — once in the
    // summary and once under "This resume says" — so take the first and walk up
    // to the <details> that contains both.
    const reworded = (
      await screen.findAllByText("Delivered the reporting pipeline migration onto PostgreSQL.")
    )[0];
    const disclosure = reworded.closest("details")!;

    expect(within(disclosure).getByText(/show original/i)).toBeInTheDocument();
    expect(
      within(disclosure).getByText(
        "Migrated the reporting pipeline onto PostgreSQL with zero downtime.",
      ),
    ).toBeInTheDocument();
  });

  it("says so plainly when a line is the profile's own wording", async () => {
    render(<TailoredResumePage />);

    const copied = (
      await screen.findAllByText("Built the payments ingestion service in Python.")
    )[0];

    expect(
      within(copied.closest("details")!).getByText(/copied from your profile/i),
    ).toBeInTheDocument();
  });

  it("does not present what it targeted as a claim that the user meets it", async () => {
    render(<TailoredResumePage />);

    expect(await screen.findByText(/what this resume was aimed at/i)).toBeInTheDocument();
    expect(screen.getByText(/not that you meet them/i)).toBeInTheDocument();
  });

  // --- the other states ------------------------------------------------------------

  it("shows the stage while generation is still running", async () => {
    detail(tailoringRunInFlight("validating"));

    render(<TailoredResumePage />);

    // The stage is named twice on purpose — in the badge beside the title and
    // in the live region below it — and the loading skeleton is a live region
    // too, so neither the text nor the role alone identifies the right node.
    const matches = await screen.findAllByText(/checking against your profile/i);
    expect(matches.some((node) => node.getAttribute("role") === "status")).toBe(true);
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("does not spin forever on a run that stopped responding", async () => {
    detail({ ...tailoringRunInFlight("generating"), is_stalled: true });

    render(<TailoredResumePage />);

    // Said twice on purpose: once in the badge beside the title and once in the
    // alert that offers the way out.
    expect((await screen.findAllByText(/stopped responding/i)).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /generate again/i })[0]).toBeInTheDocument();
  });

  it("explains a failure in plain words and offers a retry when the server allows one", async () => {
    detail(
      tailoringRunDetail({
        status: "failed",
        failed_stage: "generate",
        error_code: "ai_rate_limited",
        error_message: "The writing service was busy.",
        can_retry: true,
        result: null,
      }),
    );

    render(<TailoredResumePage />);

    expect(
      await screen.findByText("Generation failed while writing the resume"),
    ).toBeInTheDocument();
    expect(screen.getByText(/the writing service is busy/i)).toBeInTheDocument();
    // Another attempt at THIS run's inputs — its version — not whatever the
    // tailor page would default to.
    expect(screen.getByRole("link", { name: /try again/i })).toHaveAttribute(
      "href",
      "/jobs/7/tailor?version=102",
    );
  });

  it("names no stage and no reference it does not have", async () => {
    detail(
      tailoringRunDetail({
        status: "failed",
        failed_stage: null,
        error_code: null,
        error_message: null,
        can_retry: false,
        result: null,
      }),
    );

    render(<TailoredResumePage />);

    expect(await screen.findByText("Generation failed")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/failed at|stage\b|Reference:/);
  });

  it("carries the run's version and match into another attempt, and links its own match", async () => {
    detail(tailoringRunDetail({ match_result_id: 9 }));

    render(<TailoredResumePage />);

    expect(await screen.findByRole("link", { name: "Generate again" })).toHaveAttribute(
      "href",
      "/jobs/7/tailor?version=102&match=9",
    );
    expect(screen.getByRole("link", { name: "Your match for this job" })).toHaveAttribute(
      "href",
      "/jobs/7/match?match=9",
    );
  });

  it("calls a run without a job title an untitled job, never a database id", async () => {
    detail(tailoringRunDetail({ job_title: null }));

    render(<TailoredResumePage />);

    expect((await screen.findAllByText("Untitled job")).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/Job 7|#\d/);
  });

  it("keeps its heading while it cannot find the run", async () => {
    detail({ detail: "No such run.", code: "not_found" }, 404);

    render(<TailoredResumePage />);

    expect(await screen.findByText("That tailored resume does not exist")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Tailored resume" })).toBeInTheDocument();
  });

  it("does not offer a retry the server says would fail the same way", async () => {
    detail(
      tailoringRunDetail({
        status: "failed",
        failed_stage: "generate",
        error_code: "empty_profile_version",
        error_message: "Nothing to build from.",
        can_retry: false,
        result: null,
      }),
    );

    render(<TailoredResumePage />);

    expect(await screen.findByText(/another attempt would fail the same way/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("treats a missing run as an empty state, not a crash", async () => {
    server.use(
      http.get(`${BASE}/tailored-resumes/:id`, () =>
        apiError(404, "That tailored resume does not exist.", "not_found"),
      ),
    );

    render(<TailoredResumePage />);

    expect(
      await screen.findByText(/that tailored resume does not exist/i),
    ).toBeInTheDocument();
  });

  it("shows the API's own sentence and nothing from inside the server", async () => {
    // A 409 rather than a 500 because the query layer deliberately does not
    // retry one — see `retryUnlessExpected`. A 500 would be three attempts and
    // two backoffs before anything rendered, which is correct behaviour and a
    // slow way to assert this.
    server.use(
      http.get(`${BASE}/tailored-resumes/:id`, () =>
        apiError(409, "This resume has not finished generating yet.", "conflict"),
      ),
    );

    render(<TailoredResumePage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/has not finished generating yet/i);
    // No stack trace, no SQL, no storage key, no driver name.
    expect(document.body.textContent).not.toMatch(
      /Traceback|SELECT |psycopg|storage_key|tailored\/\d/,
    );
  });
});

describe("downloading", () => {
  beforeEach(() => {
    // jsdom implements neither, and the component uses both to hand the file to
    // the browser.
    URL.createObjectURL = vi.fn(() => "blob:stub");
    URL.revokeObjectURL = vi.fn();
  });

  it("uses the filename the server chose", async () => {
    const { user } = setup();
    render(<TailoredResumePage />);

    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await user.click(await screen.findByRole("button", { name: /download resume/i }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    // The name came out of Content-Disposition — the server built it from the
    // job and the version, and the client does not invent one.
    const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe("Acme-Corp-Senior-Backend-Engineer-v1.docx");
    // Not revoked in the same task as the click, which only schedules the
    // download — some browsers read the URL after that task has ended.
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    click.mockRestore();
    // The first test in this block pays for the lazy user-event import and a
    // cold binary response; it measured ~4.5s alone against vitest's 5s
    // default and timed out under a full parallel run. Nothing in it waits on
    // purpose, so the allowance is for the machine, not for the page.
  }, 15_000);

  it("shows the server's refusal rather than a broken download", async () => {
    const { user } = setup();
    server.use(
      http.get(`${BASE}/tailored-resumes/:id/artifact`, () =>
        apiError(
          409,
          "This resume contains statements that could not be traced back to your profile, so it was not approved for download.",
          "conflict",
        ),
      ),
    );

    render(<TailoredResumePage />);
    await user.click(await screen.findByRole("button", { name: /download resume/i }));

    expect(
      await screen.findByText(/not approved for download/i),
    ).toBeInTheDocument();
  });
});

function setup() {
  // Imported lazily so the mock above is installed first.
  const userEvent = require("@testing-library/user-event").default;
  return { user: userEvent.setup() };
}

describe("TailoredResumePage — who the resume is from", () => {
  // The review screen is where a user decides whether to send the file, so it
  // has to show what the file will say. For a while the generated document
  // carried no contact details at all: it passed grounding, rendered cleanly,
  // and arrived at an employer with no name on it and no way to reply. The
  // fields are transcribed from the profile version and checked exactly, which
  // is what makes showing them here safe.

  it("shows the candidate's name and how to reach them", async () => {
    render(<TailoredResumePage />);

    expect(await screen.findByText("Alex Morgan")).toBeInTheDocument();
    expect(
      screen.getByText(/alex@example\.com/),
    ).toBeInTheDocument();
    expect(screen.getByText(/\+44 7700 900123/)).toBeInTheDocument();
    expect(screen.getByText(/Bristol, United Kingdom/)).toBeInTheDocument();
  });

  it("shows each link under the words the profile stored it as", async () => {
    render(<TailoredResumePage />);

    expect(await screen.findByText("LinkedIn")).toBeInTheDocument();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
  });

  it("says the contact block was copied rather than composed", async () => {
    // The distinction the rest of the screen makes line by line: a bullet may
    // have been reworded, a phone number never is.
    render(<TailoredResumePage />);

    expect(
      await screen.findByText(/Copied from your profile version/i),
    ).toBeInTheDocument();
  });

  it("renders a document that has no contact details without inventing any", async () => {
    // A confirmed version whose resume gave no contact columns is a real state.
    // The section is absent; nothing is filled in to cover the gap.
    detail(
      tailoringRunDetail({
        result: tailoredResume({ document: tailoredDocument({ contact: null }) }),
      }),
    );
    render(<TailoredResumePage />);

    expect(await screen.findByText("Experience")).toBeInTheDocument();
    expect(screen.queryByText("Alex Morgan")).not.toBeInTheDocument();
    expect(screen.queryByText(/Copied from your profile version/i)).not.toBeInTheDocument();
  });
});
