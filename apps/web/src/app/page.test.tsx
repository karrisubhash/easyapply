import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { confirmedVersion, draftVersion } from "@/test/fixtures";
import { render, screen, within } from "@/test/render";
import { job } from "@/test/job-fixtures";
import { apiError, server } from "@/test/server";
import { tailoringRun } from "@/test/tailoring-fixtures";

import DashboardPage from "./page";

const BASE = "/api/backend";

/** A brand-new account: a profile exists, nothing has been done with it yet. */
function nothingSavedYet() {
  server.use(
    http.get(`${BASE}/resumes`, () => HttpResponse.json([])),
    http.get(`${BASE}/jobs`, () => HttpResponse.json([])),
    http.get(`${BASE}/tailored-resumes`, () => HttpResponse.json([])),
  );
}

/**
 * The card a collection lives in.
 *
 * Nothing on this page may be asserted page-wide: the three cards render the
 * same shapes, and the shared fixtures give a job and the tailored resume built
 * from it the same title and the same company.
 */
function card(title: string | RegExp): HTMLElement {
  return screen.getByRole("heading", { name: title }).closest(".rounded-lg") as HTMLElement;
}

describe("DashboardPage", () => {
  it("says each collection is still loading rather than showing it as empty", () => {
    render(<DashboardPage />);

    // Not-yet-known and empty are different facts, and the empty card carries a
    // call to action — telling someone to upload a resume while their resumes
    // are still in flight is advice the page has not earned yet.
    expect(screen.getAllByRole("status").map((node) => node.textContent)).toEqual([
      "Loading resumes",
      "Loading jobs",
      "Loading tailored resumes",
    ]);
    expect(screen.getByText("Checking…")).toBeInTheDocument();
  });

  // --- what the API actually returned ------------------------------------------

  it("shows when the current profile was confirmed", async () => {
    render(<DashboardPage />);

    const description = await screen.findByText(/^Confirmed /);
    // The stamped date, not "recently" — a confirmed version is the input to
    // every match and every tailored document, so which one and when is a fact
    // the user may need to reconcile against a document they already sent.
    expect(description).toHaveTextContent(/2026/);
    expect(within(card(/^Profile/)).getByText("Current")).toBeInTheDocument();
  });

  it("lists recent resumes with the state the file actually reached", async () => {
    render(<DashboardPage />);
    const resumes = card("Resumes");

    expect(await within(resumes).findByText("resume.pdf")).toBeInTheDocument();
    expect(within(resumes).getByText("Ready to review")).toBeInTheDocument();
    expect(within(resumes).getByRole("link", { name: /resume\.pdf/ })).toHaveAttribute(
      "href",
      "/resume/1",
    );
  });

  it("lists recent jobs with the company and the structuring state", async () => {
    render(<DashboardPage />);
    const jobs = card("Jobs");

    expect(await within(jobs).findByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(within(jobs).getByText("Acme Corp")).toBeInTheDocument();
    expect(within(jobs).getByText("Ready to review")).toBeInTheDocument();
    expect(
      within(jobs).getByRole("link", { name: /Senior Backend Engineer/ }),
    ).toHaveAttribute("href", "/jobs/1");
  });

  it("names the profile version each tailored resume was generated from", async () => {
    render(<DashboardPage />);
    const tailored = card("Tailored resumes");

    // A generated document is only auditable alongside the version it was built
    // from, and the dashboard is where the user first meets it.
    expect(await within(tailored).findByText(/From version 1/)).toBeInTheDocument();
    expect(within(tailored).getByText("Ready")).toBeInTheDocument();
    expect(
      within(tailored).getByRole("link", { name: /Senior Backend Engineer/ }),
    ).toHaveAttribute("href", "/tailored-resumes/1");
  });

  // --- the two states that block everything downstream --------------------------

  it("opens on the one thing that has to happen first when there is no profile", async () => {
    server.use(
      http.get(`${BASE}/profile`, () => apiError(404, "No profile exists.", "not_found")),
    );
    nothingSavedYet();

    render(<DashboardPage />);

    expect(await screen.findByText("You do not have a profile yet.")).toBeInTheDocument();
    expect(within(card(/^Profile/)).getByRole("link", { name: "Create profile" })).toHaveAttribute(
      "href",
      "/profile",
    );
    // A 404 here is the answer, not a failure. Alarming someone on their first
    // visit is the wrong reading of "you have not started yet".
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(within(card(/^Profile/)).queryByText("Current")).not.toBeInTheDocument();
  });

  it("says a profile with no confirmed version cannot be matched or tailored", async () => {
    server.use(
      http.get(`${BASE}/profile/versions/current`, () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      // Nothing confirmed at all — the state this test is about. The shared
      // list has a confirmed version in it, which is the other state below.
      http.get(`${BASE}/profile/versions`, () => HttpResponse.json([draftVersion()])),
    );

    render(<DashboardPage />);

    // Matching and tailoring both 409 on exactly this state. The dashboard is
    // the only place the user can learn it before walking into the refusal two
    // pages later, so it has to say so and point at the fix.
    expect(
      await screen.findByText(/Only a confirmed version can be matched or tailored/),
    ).toBeInTheDocument();
    expect(
      within(card(/^Profile/)).getByRole("link", { name: "Review and confirm" }),
    ).toHaveAttribute("href", "/profile");
    // Nothing is current, so nothing may be badged as current.
    expect(within(card(/^Profile/)).queryByText("Current")).not.toBeInTheDocument();
  });

  // --- empty, and error, per collection -----------------------------------------

  it("gives every empty collection the action that would fill it", async () => {
    nothingSavedYet();
    render(<DashboardPage />);

    const resumes = card("Resumes");
    expect(await within(resumes).findByText("No resumes uploaded yet.")).toBeInTheDocument();
    expect(within(resumes).getByRole("link", { name: "Upload a resume" })).toHaveAttribute(
      "href",
      "/resume",
    );

    const jobs = card("Jobs");
    expect(within(jobs).getByText("No jobs saved yet.")).toBeInTheDocument();
    expect(within(jobs).getByRole("link", { name: "Add a job" })).toHaveAttribute(
      "href",
      "/jobs/new",
    );

    const tailored = card("Tailored resumes");
    expect(within(tailored).getByText(/Nothing generated yet/)).toBeInTheDocument();
    // Tailoring cannot be started from here — it starts from a job — so the
    // empty card sends the user to the place where it can.
    expect(
      within(tailored).getByRole("link", { name: "Choose a job to tailor for" }),
    ).toHaveAttribute("href", "/jobs");
  });

  it("does not offer to open a list that has nothing in it", async () => {
    nothingSavedYet();
    render(<DashboardPage />);
    await within(card("Resumes")).findByText("No resumes uploaded yet.");

    expect(screen.queryByRole("link", { name: /^View all/ })).not.toBeInTheDocument();
  });

  it("contains a failed collection inside its own card", async () => {
    // A 409 rather than a 500 because the query layer deliberately does not
    // retry one — see `retryUnlessExpected`. A 500 would be three attempts and
    // two backoffs before anything rendered, and the status code is not what is
    // under test here; containment is.
    server.use(
      http.get(`${BASE}/jobs`, () =>
        apiError(409, "Saved jobs could not be read.", "conflict"),
      ),
    );

    render(<DashboardPage />);
    const jobs = card("Jobs");

    expect(await within(jobs).findByRole("alert")).toHaveTextContent(
      "Saved jobs could not be read.",
    );
    // One endpoint being down does not make the rest of the page untrue.
    expect(await within(card("Resumes")).findByText("resume.pdf")).toBeInTheDocument();
    expect(await within(card("Tailored resumes")).findByText(/From version 1/)).toBeInTheDocument();
    expect(await screen.findByText(/^Confirmed /)).toBeInTheDocument();
    // An error is not an empty collection. "No jobs saved yet" here would be
    // telling the user their saved jobs are gone.
    expect(within(jobs).queryByText("No jobs saved yet.")).not.toBeInTheDocument();
    expect(within(jobs).queryByRole("link", { name: "Add a job" })).not.toBeInTheDocument();
    // What failed is described in the API's own sentence, not in its internals.
    expect(document.body.textContent).not.toMatch(/Traceback|SELECT |psycopg|at Object\./);
  });

  it("reports a profile failure that is not a 404, rather than swallowing it", async () => {
    // Again a 409 for the retry policy's sake. The distinction under test is
    // that "you have no profile yet" and "we could not read your profile" are
    // different sentences: only the first one is a state to act on.
    server.use(
      http.get(`${BASE}/profile`, () =>
        apiError(409, "Your profile could not be read.", "conflict"),
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Your profile could not be read.");
    expect(screen.queryByText("You do not have a profile yet.")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create profile" })).not.toBeInTheDocument();
    // The collections do not depend on the profile, so they still render.
    expect(await within(card("Resumes")).findByText("resume.pdf")).toBeInTheDocument();
  });

  // --- no invented data ----------------------------------------------------------

  it("invents nothing to fill a first run: no counts, no score, no progress", async () => {
    server.use(
      http.get(`${BASE}/profile`, () => apiError(404, "No profile exists.", "not_found")),
    );
    nothingSavedYet();

    render(<DashboardPage />);
    await screen.findByText("You do not have a profile yet.");

    // Nothing has happened yet, so there is no true number to print. Any digit
    // on this screen would be one the product made up — a completion
    // percentage, a "0 of 4 steps", a strength meter — and a product that opens
    // on a fabricated statistic has taught its user to discount the grounding
    // verdict and the match verdict too.
    expect(document.body).not.toHaveTextContent(/\d/);
  });

  it("puts no percentage, score or strength meter on a populated dashboard", async () => {
    render(<DashboardPage />);
    await within(card("Resumes")).findByText("resume.pdf");

    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\d\s*%/);
    expect(text).not.toMatch(/\bscore\b|\bstrength\b|\bcompleteness\b|\brating\b|\bgrade\b/i);
    // Coverage is coverage; nothing anywhere in this product may present it as
    // a chance of an outcome.
    expect(text).not.toMatch(/\bprobability\b|\blikelihood\b|\bchance\b/i);
    // No "2 of 5 requirements", no "1 of 3 steps" — this page counts nothing.
    expect(text).not.toMatch(/\b\d+\s+of\s+\d+\b/);
  });

  it("shows a summary of recent work rather than a count of it", async () => {
    server.use(
      http.get(`${BASE}/jobs`, () =>
        HttpResponse.json(
          [1, 2, 3, 4, 5].map((id) => job({ id, job_title: `Job ${id}` })),
        ),
      ),
    );

    render(<DashboardPage />);
    const jobs = card("Jobs");

    await within(jobs).findByText("Job 1");
    expect(within(jobs).getAllByRole("listitem")).toHaveLength(3);
    // Truncating and saying where the rest live is honest. "5 jobs" or "2 more"
    // would be the page counting rather than reporting.
    expect(jobs).not.toHaveTextContent("5");
    expect(within(jobs).getByRole("link", { name: "View all jobs" })).toHaveAttribute(
      "href",
      "/jobs",
    );
  });

  // --- what the rows must never render --------------------------------------------

  it("renders a tailored run with no job attached without printing 'undefined'", async () => {
    server.use(
      http.get(`${BASE}/tailored-resumes`, () =>
        HttpResponse.json([
          tailoringRun({ job_title: null, company_name: null, profile_version_number: null }),
        ]),
      ),
    );

    render(<DashboardPage />);
    const tailored = card("Tailored resumes");

    expect(await within(tailored).findByText("Untitled job")).toBeInTheDocument();
    expect(tailored).not.toHaveTextContent(/#\d/);
    expect(tailored).not.toHaveTextContent("undefined");
    expect(tailored).not.toHaveTextContent("null");
  });

  it("never calls a resume that failed grounding ready, and offers no file", async () => {
    server.use(
      http.get(`${BASE}/tailored-resumes`, () =>
        HttpResponse.json([
          tailoringRun({ grounding_status: "failed", error_count: 1, has_artifact: false }),
        ]),
      ),
    );

    render(<DashboardPage />);
    const tailored = card("Tailored resumes");

    expect(await within(tailored).findByText("Not approved — no file")).toBeInTheDocument();
    expect(within(tailored).queryByText("Ready")).not.toBeInTheDocument();
    // The only route to a rejected document is the review page, where the
    // refusal and the sentence that caused it are. A download from the
    // dashboard would be a way around the gate.
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /download/i })).not.toBeInTheDocument();
  });

  it("links only at pages that exist", async () => {
    render(<DashboardPage />);
    await within(card("Resumes")).findByText("resume.pdf");

    // Every entry is a real route under src/app. Enumerated rather than spot
    // checked, so a new card cannot quietly add a link to a page nobody built.
    const hrefs = screen.getAllByRole("link").map((link) => link.getAttribute("href"));
    expect([...new Set(hrefs)].sort()).toEqual([
      "/jobs",
      "/jobs/1",
      "/jobs/new",
      "/profile",
      "/resume",
      "/resume/1",
      "/tailored-resumes",
      "/tailored-resumes/1",
    ]);
  });
});

/**
 * Regressions for two bugs the dashboard shipped with.
 *
 * Both are §11 failures: a screen that neither succeeds, nor fails, nor tells
 * the user which.
 */
describe("DashboardPage — states that used to have no branch", () => {
  it("reports a failed current-version lookup instead of loading forever", async () => {
    // A 404 here means "nothing confirmed yet" and is handled. Every OTHER
    // failure fell through to the loading sentence on a query that had already
    // settled, so the card sat on "Loading your current version…" permanently.
    server.use(
      http.get(`${BASE}/profile/versions/current`, () =>
        apiError(409, "The version service refused.", "conflict"),
      ),
    );

    render(<DashboardPage />);

    expect(
      await screen.findByText(/current version could not be loaded/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/loading your current version/i),
    ).not.toBeInTheDocument();
    // And the server's own sentence is surfaced, not swallowed.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /the version service refused/i,
    );
  });

  it("reports a failed profile lookup instead of loading a version it will never ask for", async () => {
    // The other end of the same sentence. `useCurrentVersion` is gated on the
    // profile query succeeding, so when the PROFILE fails the current-version
    // query is never dispatched at all: it stays `isPending` with nothing in
    // flight, for good. With no branch of its own the card fell through to
    // "Loading your current version…" and stayed there — a spinner for a
    // request that will never be made, directly above an alert explaining that
    // the API is unreachable. Reproduced in the browser by stopping the API,
    // which answers 502 — asserted here with a 409 for the reason the test
    // below gives: a 502 is retried twice with exponential backoff first, and
    // the branch under test does not depend on which failure it was.
    server.use(
      http.get(`${BASE}/profile`, () =>
        apiError(409, "The profile service refused.", "conflict"),
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByText(/your profile could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByText(/loading your current version/i)).not.toBeInTheDocument();
    // And the reason is still stated, rather than only the card's summary.
    expect(
      await screen.findByText(/the profile service refused/i),
    ).toBeInTheDocument();
  });

  it("does not mistake a failed lookup for having no confirmed version", async () => {
    // A 409 rather than a 5xx: `retryUnlessExpected` does not retry a conflict,
    // so it renders at once. A 502 takes three attempts and two exponential
    // backoffs first — correct behaviour, and a slow way to assert this.
    server.use(
      http.get(`${BASE}/profile/versions/current`, () =>
        apiError(409, "That conflicts with the current state.", "conflict"),
      ),
    );

    render(<DashboardPage />);

    await screen.findByText(/current version could not be loaded/i);
    // "Review and confirm" would send the user to fix a problem they do not
    // have; the two states must not collapse into one message.
    expect(
      screen.queryByRole("link", { name: /review and confirm/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/no version has been confirmed yet/i),
    ).not.toBeInTheDocument();
  });

  it("does not tell a user with data that they have none while it is still loading", async () => {
    // The empty branch is guarded on `!query.isPending`. Without that guard
    // every card would advise a user with a full account to go and upload
    // something, for as long as the requests were in flight.
    render(<DashboardPage />);

    expect(screen.queryByText("No resumes uploaded yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("No jobs saved yet.")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/nothing generated yet/i),
    ).not.toBeInTheDocument();
    // And it really was mid-flight when that was asserted.
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });

  it("links a tailored resume to its review page, never straight at the file", async () => {
    // The grounding verdict and the offending sentence live on the review page.
    // A download link here would be a way around the screen that explains why a
    // document was or was not approved.
    render(<DashboardPage />);

    const row = await within(card("Tailored resumes")).findByRole("link", {
      name: /senior backend engineer/i,
    });
    expect(row).toHaveAttribute("href", "/tailored-resumes/1");

    const hrefs = Array.from(document.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs.some((href) => href?.includes("/artifact"))).toBe(false);
  });

  it("does not call confirmed versions unconfirmed when none of them is current", async () => {
    // A version confirmed with "Make this the current version" unticked. The
    // card said nothing had been confirmed and sent the user to confirm again.
    server.use(
      http.get(`${BASE}/profile/versions/current`, () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([confirmedVersion({ is_current: false })]),
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByText(/none is marked current/)).toBeInTheDocument();
    expect(screen.queryByText(/no version has been confirmed yet/i)).not.toBeInTheDocument();
    expect(
      within(card(/^Profile/)).getByRole("link", { name: "Version history" }),
    ).toHaveAttribute("href", "/profile/versions");
  });

  it("reports a version list it could not read instead of calling nothing confirmed", async () => {
    server.use(
      http.get(`${BASE}/profile/versions/current`, () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      http.get(`${BASE}/profile/versions`, () =>
        apiError(409, "The versions could not be listed.", "conflict"),
      ),
    );

    render(<DashboardPage />);

    expect(await screen.findByText("Your versions could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText(/no version has been confirmed yet/i)).not.toBeInTheDocument();
    expect(screen.getByText("The versions could not be listed.")).toBeInTheDocument();
    expect(
      within(card(/^Profile/)).queryByRole("link", { name: "Review and confirm" }),
    ).not.toBeInTheDocument();
  });

  it("names each View all for the list it opens", async () => {
    render(<DashboardPage />);

    await within(card("Jobs")).findByRole("link", { name: "View all jobs" });
    const names = screen
      .getAllByRole("link", { name: /^View all/ })
      .map((link) => link.getAttribute("aria-label"));
    expect(names).toEqual(["View all resumes", "View all jobs", "View all tailored resumes"]);
    expect(document.title).toBe("Dashboard · EasyApply");
  });
});
