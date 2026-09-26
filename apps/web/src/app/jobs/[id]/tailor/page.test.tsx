import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TailoringRunDetail } from "@easyapply/shared";

import { render, screen, within } from "@/test/render";
import { confirmedVersion } from "@/test/fixtures";
import { jobDetail, jobRequirementsResult, queuedRun, run } from "@/test/job-fixtures";
import { apiError, server } from "@/test/server";
import {
  tailoringRun,
  tailoringRunDetail,
  tailoringRunInFlight,
} from "@/test/tailoring-fixtures";

import TailorJobPage from "./page";

// This page reads all three navigation hooks: the job id from the route, the
// carried match from the query string, and the router it uses to jump straight
// to a run that finished before the POST answered.
const nav = vi.hoisted(() => ({
  push: vi.fn(),
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useSearchParams: () => nav.search,
  useRouter: () => ({
    push: nav.push,
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

const BASE = "/api/backend";

beforeEach(() => {
  nav.push.mockClear();
  nav.search = new URLSearchParams();
  // One confirmed version is the ordinary case, and the picker hides itself for
  // it. The shared default is a draft-only profile, which makes the picker
  // render its own "Go to profile" link and would leave the refusal tests below
  // unable to say which link the user was actually offered.
  server.use(
    http.get(`${BASE}/profile/versions`, () => HttpResponse.json([confirmedVersion()])),
  );
});

function runsForJob(...runs: unknown[]) {
  server.use(http.get(`${BASE}/jobs/:id/tailored-resumes`, () => HttpResponse.json(runs)));
}

function activeRun(status: string, overrides: Record<string, unknown> = {}) {
  return {
    ...tailoringRun({
      status: status as "queued",
      is_terminal: false,
      finished_at: null,
      grounding_status: null,
      error_count: null,
      warning_count: null,
      has_artifact: false,
    }),
    ...overrides,
  };
}

/** The run detail the page polls while a generation is active. */
function polls(body: TailoringRunDetail | Record<string, unknown>) {
  server.use(http.get(`${BASE}/tailored-resumes/:id`, () => HttpResponse.json(body)));
}

function refuses(status: number, detail: string) {
  server.use(
    http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
      apiError(status, detail, status === 409 ? "conflict" : "unexpected_error"),
    ),
  );
}

describe("TailorJobPage — before anything can be generated", () => {
  it("does not offer Generate for a job whose posting has not been read yet", async () => {
    // Generating against a job with no structured requirements is a 409 at the
    // API. Offering the button would walk the user into that refusal, so the
    // page explains the state and points at the screen that resolves it.
    server.use(
      http.get(`${BASE}/jobs/:id`, () =>
        HttpResponse.json(jobDetail({ latest_run: queuedRun() })),
      ),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText("This job has not been read yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the job" })).toHaveAttribute(
      "href",
      "/jobs/1",
    );
    expect(
      screen.queryByRole("button", { name: /generate resume/i }),
    ).not.toBeInTheDocument();
    // Nor the empty state, which invites a generation that cannot happen.
    expect(screen.queryByText("Nothing generated for this job yet")).not.toBeInTheDocument();
  });

  it("says the same for a job that was never processed at all", async () => {
    // `latest_run` is null for a job that has only just been captured — a
    // different shape from a run in progress, and the same refusal at the API.
    server.use(
      http.get(`${BASE}/jobs/:id`, () => HttpResponse.json(jobDetail({ latest_run: null }))),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText("This job has not been read yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /generate resume/i }),
    ).not.toBeInTheDocument();
  });

  it("does not offer Generate for a job whose structuring failed", async () => {
    const failed = run({
      status: "failed",
      failed_stage: "structure",
      error_code: "structuring_failed",
      error_message: "The posting could not be structured.",
    });
    server.use(
      http.get(`${BASE}/jobs/:id`, () => HttpResponse.json(jobDetail({ latest_run: failed }))),
      // What the API answers when no attempt has produced a reading.
      http.get(`${BASE}/jobs/:id/requirements`, () =>
        HttpResponse.json(jobRequirementsResult({ run: failed, requirements: null })),
      ),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText("This job has not been read yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /generate resume/i }),
    ).not.toBeInTheDocument();
  });

  it("offers Generate after a failed reprocess, against the earlier reading", async () => {
    // The server tailors against the newest completed reading, so a failed
    // reprocess must not turn the page into "never read".
    const failed = run({
      id: 2,
      attempt: 2,
      status: "failed",
      failed_stage: "structure",
      error_code: "structuring_failed",
      error_message: "The posting could not be structured.",
    });
    server.use(
      http.get(`${BASE}/jobs/:id`, () => HttpResponse.json(jobDetail({ latest_run: failed }))),
      http.get(`${BASE}/jobs/:id/requirements`, () =>
        HttpResponse.json(jobRequirementsResult({ run: run({ id: 1, attempt: 1 }) })),
      ),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(
      await screen.findByRole("button", { name: /generate resume/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/written against the earlier reading/)).toBeInTheDocument();
    expect(screen.queryByText("This job has not been read yet")).not.toBeInTheDocument();
  });

  it("shows a loading state rather than a bare page while the job is fetched", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get(`${BASE}/jobs/:id`, async () => {
        await gate;
        return HttpResponse.json(jobDetail());
      }),
    );

    render(<TailorJobPage />);

    expect(await screen.findByText("Loading job")).toBeInTheDocument();
    // Nothing is offered until the job has said whether it can be tailored
    // against at all.
    expect(
      screen.queryByRole("button", { name: /generate resume/i }),
    ).not.toBeInTheDocument();

    release();
    expect(
      await screen.findByRole("button", { name: "Generate resume" }),
    ).toBeInTheDocument();
  });

  it("treats a job that does not exist as an empty state, not a crash", async () => {
    server.use(http.get(`${BASE}/jobs/:id`, () => apiError(404, "No such job.", "not_found")));

    render(<TailorJobPage />);

    expect(await screen.findByText("That job does not exist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to jobs" })).toHaveAttribute("href", "/jobs");
  });

  it("shows the empty state when nothing has been generated for this job", async () => {
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText("Nothing generated for this job yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate resume" })).toBeEnabled();
    // There is no last one to open, so the link that would lead nowhere is not
    // offered.
    expect(
      screen.queryByRole("link", { name: /open the last one/i }),
    ).not.toBeInTheDocument();
  });
});

describe("TailorJobPage — generating", () => {
  it("posts once and shows the run that came back", async () => {
    // The list is empty until the run exists, so the run arriving is a real
    // transition rather than a fixture that was there all along.
    let listCalls = 0;
    let body: unknown;
    server.use(
      http.get(`${BASE}/jobs/:id/tailored-resumes`, () => {
        listCalls += 1;
        return HttpResponse.json(listCalls === 1 ? [] : [activeRun("queued")]);
      }),
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    // The server's own stage, not a spinner standing in for it.
    const queued = await screen.findAllByText("Queued…");
    expect(queued.some((node) => node.getAttribute("role") === "status")).toBe(true);
    // And the run is listed, so leaving this page does not lose it.
    expect(await screen.findByText(/Attempt 1/)).toBeInTheDocument();
    expect(screen.queryByText("Nothing generated for this job yet")).not.toBeInTheDocument();
    // No version was picked and no match was carried, so neither is invented.
    expect(body).toEqual({});
  });

  it("does not start two generations when Generate is pressed twice", async () => {
    let posts = 0;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, () => {
        posts += 1;
        return HttpResponse.json(tailoringRunInFlight("generating"), { status: 201 });
      }),
    );
    runsForJob();
    polls(tailoringRunInFlight("generating"));

    render(<TailorJobPage />);
    const button = await screen.findByRole("button", { name: "Generate resume" });

    await userEvent.click(button);
    await userEvent.click(button);

    // A second run is a second document and a second thing for the user to
    // choose between, for no reason they asked for.
    expect(posts).toBe(1);
    expect(screen.getByRole("button", { name: "Generating…" })).toBeDisabled();
  });

  it("disables Generate while the POST itself is still open", async () => {
    // The gap between the click and the run existing is the window a
    // double-click actually lands in, so the button has to close before the
    // response arrives rather than after it.
    let posts = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async () => {
        posts += 1;
        await gate;
        return HttpResponse.json(tailoringRunInFlight("generating"), { status: 201 });
      }),
    );
    runsForJob();
    polls(tailoringRunInFlight("generating"));

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    const starting = await screen.findByRole("button", { name: "Starting…" });
    expect(starting).toBeDisabled();
    await userEvent.click(starting);
    expect(posts).toBe(1);

    release();
    expect(await screen.findByRole("button", { name: "Generating…" })).toBeDisabled();
  });

  it("goes straight to a run that had already finished when the POST answered", async () => {
    // An inline queue, or a profile small enough to render immediately, can
    // finish before the response comes back. There is nothing to watch, so
    // waiting here would be waiting for something that already happened.
    runsForJob();
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
        HttpResponse.json(tailoringRunDetail({ id: 42 }), { status: 201 }),
      ),
    );

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    expect(nav.push).toHaveBeenCalledWith("/tailored-resumes/42");
  });

  it("sends the profile version the user picked, by its id and not its number", async () => {
    // `id` and `version_number` are different numbers on the same row. Sending
    // the number would address a different version, and §8 only means something
    // if the document was built from the version the user actually chose.
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([
          confirmedVersion({ id: 102, version_number: 1, is_current: false }),
          confirmedVersion({ id: 205, version_number: 2, is_current: true }),
        ]),
      ),
    );
    runsForJob();
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Build from"), "205");
    await userEvent.click(screen.getByRole("button", { name: "Generate resume" }));

    expect(body).toEqual({ profile_version_id: 205 });
  });

  it("carries the match it was opened from, and says what that does not mean", async () => {
    // A link with only ?match= (older links, bookmarks). The match fixture
    // judged row 3, so that is the version the request must name beside it —
    // the server refuses a match for any other version.
    nav.search = new URLSearchParams("match=42");
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([
          confirmedVersion({ id: 3, version_number: 2, is_current: false }),
          confirmedVersion({ id: 205, version_number: 3, is_current: true }),
        ]),
      ),
    );
    runsForJob();
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);

    // §13: a match orders the emphasis. It does not turn a requirement nothing
    // evidenced into a claim the document is allowed to make.
    expect(
      await screen.findByText(/A requirement the\s+match could not evidence stays unevidenced/),
    ).toBeInTheDocument();

    // The match's version is pre-selected, not "current".
    expect(screen.getByLabelText("Build from")).toHaveValue("3");
    await userEvent.click(screen.getByRole("button", { name: "Generate resume" }));
    expect(body).toEqual({ profile_version_id: 3, match_result_id: 42 });
  });

  it("drops the match, and says so, when another version is picked", async () => {
    nav.search = new URLSearchParams("version=3&match=42");
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([
          confirmedVersion({ id: 3, version_number: 2, is_current: false }),
          confirmedVersion({ id: 205, version_number: 3, is_current: true }),
        ]),
      ),
    );
    runsForJob();
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);
    await screen.findByText(/Using your match for this job \(version 2\)/);

    await userEvent.selectOptions(screen.getByLabelText("Build from"), "205");
    expect(
      screen.getByText(/judged version 2, so it is not used for version 3/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Match version 3" })).toHaveAttribute(
      "href",
      "/jobs/1/match?version=205",
    );

    await userEvent.click(screen.getByRole("button", { name: "Generate resume" }));
    expect(body).toEqual({ profile_version_id: 205 });
  });

  it("shows the server's refusal of a match for another version, and points at matching", async () => {
    nav.search = new URLSearchParams("version=3&match=42");
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([confirmedVersion({ id: 3, version_number: 2, is_current: true })]),
      ),
    );
    runsForJob();
    refuses(
      409,
      "That match was made against a different profile version. Match this version against the job first, or tailor without the match.",
    );

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    expect(
      await screen.findByText(/That match was made against a different profile version/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to match" })).toHaveAttribute(
      "href",
      "/jobs/1/match?version=3",
    );
  });

  it("names the version explicitly when none is current", async () => {
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([confirmedVersion({ id: 102, version_number: 1, is_current: false })]),
      ),
    );
    runsForJob();
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));
    expect(body).toEqual({ profile_version_id: 102 });
  });

  it("says when the run it started has finished, with a way to it", async () => {
    let listCalls = 0;
    server.use(
      http.get(`${BASE}/jobs/:id/tailored-resumes`, () => {
        listCalls += 1;
        return HttpResponse.json(listCalls === 1 ? [] : [activeRun("queued")]);
      }),
      http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
        HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 }),
      ),
    );
    polls(tailoringRunDetail());

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    const link = await screen.findByRole("link", { name: "Review the tailored resume" });
    expect(link).toHaveAttribute("href", "/tailored-resumes/1");
    expect(screen.getByText("Your tailored resume is ready.")).toBeInTheDocument();
    expect(link.closest("[aria-live=polite]")).not.toBeNull();
  });

  it("reports versions it could not read instead of calling the profile unconfirmed", async () => {
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        apiError(409, "The versions could not be listed.", "conflict"),
      ),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText("The versions could not be listed.")).toBeInTheDocument();
    expect(screen.queryByText(/no confirmed profile version/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate resume" })).toBeDisabled();
  });

  it("ignores a match parameter that is not a match id", async () => {
    // A hand-edited or truncated URL must not put NaN on the wire, where the
    // API would reject the whole request over a hint that only affects order.
    nav.search = new URLSearchParams("match=not-a-number");
    runsForJob();
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 });
      }),
    );
    polls(tailoringRunInFlight("queued"));

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    expect(body).toEqual({});
    expect(screen.queryByText(/Using your match for this job/)).not.toBeInTheDocument();
  });
});

describe("TailorJobPage — refusals", () => {
  it("shows the server's own sentence about a draft profile, and points at the profile", async () => {
    runsForJob();
    refuses(
      409,
      "This profile has no confirmed version yet. Confirm a version before generating a resume.",
    );

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    // The API decides which rule was broken; the page repeats its sentence
    // rather than paraphrasing a rule the frontend does not own.
    expect(
      await screen.findByText(
        "This profile has no confirmed version yet. Confirm a version before generating a resume.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to profile" })).toHaveAttribute(
      "href",
      "/profile",
    );
  });

  it("points at the job instead when the refusal is about the job", async () => {
    runsForJob();
    refuses(
      409,
      "This job has not been structured yet, so there are no requirements to write against.",
    );

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    expect(
      await screen.findByText(/there are no requirements to write against/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to job" })).toHaveAttribute("href", "/jobs/1");
    expect(screen.queryByRole("link", { name: "Go to profile" })).not.toBeInTheDocument();
  });

  it("reports a failure that is not a refusal, without anything from inside the server", async () => {
    runsForJob();
    refuses(500, "Something went wrong.");

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Generate resume" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong.");
    expect(document.body.textContent).not.toMatch(/Traceback|SELECT |psycopg|storage_key/);
  });

  it("reports a list it could not read instead of calling the job untouched", async () => {
    // An unreadable list is not an absence of attempts. The empty state here
    // would tell the user nothing has ever been generated for this job, which
    // is a fact the page does not have.
    server.use(
      http.get(`${BASE}/jobs/:id/tailored-resumes`, () =>
        apiError(502, "The API could not be reached.", "bad_gateway"),
      ),
    );

    render(<TailorJobPage />);

    // A reachability failure is the one kind the query layer DOES retry — three
    // attempts and two backoffs before anything renders, which is why this one
    // waits longer than the default.
    expect(
      await screen.findByText("The API could not be reached.", undefined, { timeout: 8000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Nothing generated for this job yet")).not.toBeInTheDocument();
  });
});

describe("TailorJobPage — the wait", () => {
  it("names the stage the server is actually in while it checks the document", async () => {
    runsForJob(activeRun("validating"));
    polls(tailoringRunInFlight("validating"));

    render(<TailorJobPage />);

    // The stage is named twice on purpose — in the badge on the listed attempt
    // and in the live region — so neither the text nor the role alone picks out
    // the right node.
    const labels = await screen.findAllByText("Checking against your profile…");
    expect(labels.some((node) => node.getAttribute("role") === "status")).toBe(true);
    expect(
      screen.getByText(/Checking every generated line against the profile version/),
    ).toBeInTheDocument();
  });

  it("names a different stage once the server has moved on to the file", async () => {
    // The point of naming stages is that they change. One label for all of them
    // is a spinner with words on it.
    runsForJob(activeRun("rendering"));
    polls(tailoringRunInFlight("rendering"));

    render(<TailorJobPage />);

    const labels = await screen.findAllByText("Preparing the file…");
    expect(labels.some((node) => node.getAttribute("role") === "status")).toBe(true);
    expect(screen.getByText("Writing the document file.")).toBeInTheDocument();
  });

  it("says a run stopped responding instead of spinning forever", async () => {
    runsForJob(activeRun("generating", { is_stalled: true }));
    polls({ ...tailoringRunInFlight("generating"), is_stalled: true });

    render(<TailorJobPage />);

    expect(await screen.findByText("This generation stopped responding")).toBeInTheDocument();
    // Nothing half-written was kept, which is what makes generating again an
    // obvious move rather than a risk.
    expect(screen.getByText(/Nothing has been saved/)).toBeInTheDocument();
    // §11: no stage line is left running underneath the alert.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    // The way out of this state is deliberately not asserted here: the page
    // tells the user generating again is safe while the only Generate button
    // is still disabled by this very run. Reported, not papered over.
  });

  it("names the attempt number, so a second wait is not mistaken for the first", async () => {
    runsForJob(activeRun("generating", { attempt: 3 }));
    polls({ ...tailoringRunInFlight("generating"), attempt: 3 });

    render(<TailorJobPage />);

    expect(await screen.findByText("Attempt 3.")).toBeInTheDocument();
  });
});

describe("TailorJobPage — previous attempts", () => {
  it("lists each attempt with its number and the profile version it used", async () => {
    // §8: a resume the user cannot trace to a specific snapshot is a document
    // they cannot check. This list is where an old one gets traced.
    runsForJob(
      tailoringRun({ id: 9, attempt: 2, profile_version_number: 3 }),
      tailoringRun({ id: 4, attempt: 1, profile_version_number: 1 }),
    );

    render(<TailorJobPage />);

    const attempts = within(await screen.findByRole("list")).getAllByRole("listitem");
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toHaveTextContent(/Attempt 2 · from version 3/);
    expect(attempts[1]).toHaveTextContent(/Attempt 1 · from version 1/);
    // Every attempt is reachable, and the newest has its own shortcut.
    expect(within(attempts[0]).getByRole("link")).toHaveAttribute("href", "/tailored-resumes/9");
    expect(within(attempts[1]).getByRole("link")).toHaveAttribute("href", "/tailored-resumes/4");
    expect(screen.getByRole("link", { name: /open the last one/i })).toHaveAttribute(
      "href",
      "/tailored-resumes/9",
    );
  });

  it("distinguishes a finished document from one that needs review", async () => {
    // Completed and approved are different facts, and this list is where the
    // user decides which attempt to open. Calling both "Ready" would send them
    // to download a document that has no file.
    runsForJob(
      tailoringRun({
        id: 9,
        attempt: 2,
        grounding_status: "failed",
        error_count: 1,
        has_artifact: false,
      }),
      tailoringRun({ id: 4, attempt: 1 }),
    );

    render(<TailorJobPage />);

    const attempts = within(await screen.findByRole("list")).getAllByRole("listitem");
    expect(attempts[0]).toHaveTextContent("Not approved — no file");
    expect(attempts[1]).toHaveTextContent("Ready");
  });

  it("omits the version rather than printing undefined when a run has none recorded", async () => {
    runsForJob(tailoringRun({ profile_version_number: null }));

    render(<TailorJobPage />);

    expect(await screen.findByText("Attempt 1")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/undefined|null|from version/);
  });
});

/**
 * Regressions for three bugs this page shipped with, found by reading it
 * against its own docstring.
 *
 * All three came from the same line: `inFlight` took the polled run as ongoing
 * without asking whether it still was.
 */
describe("TailorJobPage — states that used to be dead ends", () => {
  it("lets the user act on a stalled generation instead of disabling the only way out", async () => {
    // The alert says "generating again is safe". It used to say that beside a
    // disabled Generate button, because a stalled run counted as in-flight —
    // advice the user could read and not follow.
    const stalled = activeRun("generating", { is_stalled: true });
    runsForJob(stalled);
    polls({ ...tailoringRunInFlight("generating"), is_stalled: true });

    render(<TailorJobPage />);

    expect(await screen.findByText(/stopped responding/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate resume/i })).toBeEnabled();
  });

  it("does not describe a stalled run as work still in progress", async () => {
    runsForJob(activeRun("generating", { is_stalled: true }));
    polls({ ...tailoringRunInFlight("generating"), is_stalled: true });

    render(<TailorJobPage />);
    await screen.findByText(/stopped responding/i);

    expect(
      screen.queryByText(/this page updates itself while the work runs/i),
    ).not.toBeInTheDocument();
  });

  it("stops calling a run ongoing the moment the poll says it finished", async () => {
    // The listing lags the detail poll by up to one interval. During that gap
    // the page used to render "Ready…" as though it were a pipeline stage,
    // under "this page updates itself while the work runs", with Generate still
    // disabled — a finished document presented as unfinished work.
    runsForJob(activeRun("generating"));
    polls(tailoringRunDetail({ status: "completed" }));

    render(<TailorJobPage />);

    await screen.findByRole("button", { name: /generate resume/i });
    expect(screen.getByRole("button", { name: /generate resume/i })).toBeEnabled();
    expect(
      screen.queryByText(/this page updates itself while the work runs/i),
    ).not.toBeInTheDocument();
    // "Ready" is a verdict, not a stage. It must never appear as one.
    expect(screen.queryByText(/^Ready…$/)).not.toBeInTheDocument();
  });

  it("stops calling a run ongoing once the listing has caught up with it", async () => {
    // The mirror image of the test above, and the one it left uncovered. Here
    // the LISTING is the fresh one: the deterministic generator finishes in
    // well under one poll interval, so by the time anything refetches, the run
    // is already terminal in the list — which means the detail poll is never
    // enabled and `polled` is empty. The page then fell back to the POST's own
    // response, a snapshot frozen at QUEUED that nothing ever refetches, and
    // reported "Queued…" under a disabled Generate button for the rest of the
    // session, beside a Previous attempts list showing the finished document.
    // Only a full page reload cleared it.
    runsForJob(tailoringRun({ id: 1, status: "completed" }));
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
        HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 }),
      ),
    );

    render(<TailorJobPage />);

    const generate = await screen.findByRole("button", { name: /generate resume/i });
    await userEvent.click(generate);

    // The button comes back, because there is nothing left to wait for.
    await screen.findByRole("button", { name: /generate resume/i });
    expect(screen.getByRole("button", { name: /generate resume/i })).toBeEnabled();
    expect(screen.queryByText(/^Queued…$/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/this page updates itself while the work runs/i),
    ).not.toBeInTheDocument();
  });

  it("still says something while the POST is the only thing that knows the run exists", async () => {
    // The gap the fallback legitimately covers, asserted so that closing the
    // bug above does not close this with it: between the POST answering and the
    // list refetch landing, the page must not look as though nothing happened.
    runsForJob();
    server.use(
      http.post(`${BASE}/jobs/:id/tailored-resumes`, () =>
        HttpResponse.json(tailoringRunInFlight("queued"), { status: 201 }),
      ),
    );

    render(<TailorJobPage />);
    await userEvent.click(await screen.findByRole("button", { name: /generate resume/i }));

    expect(await screen.findByText(/^Queued…$/)).toBeInTheDocument();
  });

  it("names the version it will build from even when there is only one", async () => {
    // The module docstring promises the version is stated "on every screen
    // here, before generation and after it". With a single confirmed version
    // the picker renders no select, and the page used to name nothing at all.
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([confirmedVersion({ id: 102, version_number: 7 })]),
      ),
    );
    runsForJob();

    render(<TailorJobPage />);

    expect(await screen.findByText(/version 7/i)).toBeInTheDocument();
  });

  it("does not point the empty state at a control that is not on the page", async () => {
    server.use(
      http.get(`${BASE}/profile/versions`, () => HttpResponse.json([confirmedVersion()])),
    );
    runsForJob();

    render(<TailorJobPage />);
    await screen.findByText(/nothing generated for this job yet/i);

    // There is no select to choose "above" when only one version is confirmed.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/version above/i);
  });
});
