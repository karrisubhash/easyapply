import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen, within } from "@/test/render";
import { confirmedVersion } from "@/test/fixtures";
import { matchDetail, matchSummary, requirementResult } from "@/test/match-fixtures";
import { apiError, server } from "@/test/server";

import JobMatchPage from "./page";

const nav = vi.hoisted(() => ({ search: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useSearchParams: () => nav.search,
}));

const BASE = "/api/backend";

beforeEach(() => {
  nav.search = new URLSearchParams();
  // The page now shows the match OF the selected version, so the versions and
  // the matches have to describe one account. The match fixture judged row 3,
  // version number 2; here that is the one confirmed, current version.
  server.use(
    http.get(`${BASE}/profile/versions`, () =>
      HttpResponse.json([confirmedVersion({ id: 3, version_number: 2, is_current: true })]),
    ),
  );
});

/** Three confirmed versions, rows 11/12/13, version 3 current. */
function threeVersions(current = 13) {
  server.use(
    http.get(`${BASE}/profile/versions`, () =>
      HttpResponse.json(
        [11, 12, 13].map((id) =>
          confirmedVersion({ id, version_number: id - 10, is_current: id === current }),
        ),
      ),
    ),
  );
}

/** Serve each match by its id, judged by the version the id implies. */
function matchesFor(...entries: { id: number; versionId: number }[]) {
  server.use(
    http.get(`${BASE}/jobs/:id/matches`, () =>
      HttpResponse.json(
        entries.map(({ id, versionId }) =>
          matchSummary({ id, profile_version_id: versionId, profile_version_number: versionId - 10 }),
        ),
      ),
    ),
    http.get(`${BASE}/matches/:id`, ({ params }) => {
      const entry = entries.find(({ id }) => id === Number(params.id))!;
      return HttpResponse.json(
        matchDetail({
          id: entry.id,
          profile_version_id: entry.versionId,
          profile_version_number: entry.versionId - 10,
        }),
      );
    }),
  );
}

function noExistingMatch() {
  server.use(http.get(`${BASE}/jobs/:id/matches`, () => HttpResponse.json([])));
}

describe("JobMatchPage", () => {
  it("shows the existing match for this job, with its requirement verdicts", async () => {
    render(<JobMatchPage />);

    expect(await screen.findByText("Requirement coverage")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("Rust")).toBeInTheDocument();
  });

  it("separates required from preferred rather than merging them", async () => {
    // §8: a missing preferred requirement is not a failed qualification, and
    // the page must not let the two look like the same fact.
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    const requiredAxis = screen.getByTestId("axis-required");
    const preferredAxis = screen.getByTestId("axis-preferred");

    expect(requiredAxis).toHaveTextContent("1 of 4 matched");
    expect(preferredAxis).toHaveTextContent("0 of 1 matched");

    // And again at the requirement level, as separate labelled groups.
    expect(within(screen.getByTestId("skill-required")).getByText("Rust")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("skill-preferred")).getByText("Kubernetes"),
    ).toBeInTheDocument();
  });

  it("shows matched, missing and uncertain as three distinct verdicts", async () => {
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.getAllByText("Matched").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Uncertain").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not found").length).toBeGreaterThan(0);
  });

  it("shows the evidence behind a matched requirement", async () => {
    // §5 and §27: "what profile evidence caused this match?" is the question
    // this screen exists to answer, so the evidence has to be on the page
    // rather than behind an interaction.
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.getByText("Skill: Python")).toBeInTheDocument();
    expect(screen.getByText("6 years stated")).toBeInTheDocument();
    expect(
      screen.getByText("Experience: Backend Developer at Acme Corp"),
    ).toBeInTheDocument();
    expect(screen.getByText("Django REST API development")).toBeInTheDocument();
  });

  it("grades each piece of evidence and says why it did or did not count", async () => {
    // The question the engine's own benchmark found users cannot otherwise
    // answer: why did listing a skill satisfy one requirement and not another?
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(
      screen.getAllByText("Listed and demonstrated in paid work").length,
    ).toBeGreaterThan(0);
    // Evidence that fell short is SHOWN, with the reason, rather than hidden —
    // it is how an uncertain verdict explains itself.
    expect(screen.getByText("Mentioned only")).toBeInTheDocument();
    expect(
      screen.getByText(/does not show the skill was used/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Evaluated Django for the payments rewrite/),
    ).toBeInTheDocument();
  });

  it("explains every verdict, including the ones that found nothing", async () => {
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    // Two of them: the required Rust and the preferred Kubernetes. Both say
    // in words that nothing was found, rather than being rendered as an
    // absence the reader has to interpret.
    expect(screen.getAllByText(/No qualifying evidence was found/)).toHaveLength(2);
    expect(screen.getByText(/A mention is not a claim of proficiency/)).toBeInTheDocument();
    expect(screen.getByText(/counts as a related field/)).toBeInTheDocument();
  });

  it("names the algorithm version, profile version and as-of date", async () => {
    // §14 and §15: a stored verdict is only interpretable alongside the rules
    // that reached it and the inputs it was reached from.
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.getByText("Matching v5")).toBeInTheDocument();
    expect(screen.getByText(/Measured against/)).toBeInTheDocument();
  });

  it("names the profile version the way the user names it", async () => {
    /**
     * The fixture's version is row id 3 and version NUMBER 2, and the screen
     * must say 2.
     *
     * It said 3. `profile_version_id` — a primary key — was rendered under the
     * label "Profile version", and the two are equal on a freshly truncated
     * database, so the unit suite, the browser suite and every demo agreed it
     * was right. On the real development profile, which has twelve versions,
     * match 1 was run against version 1 and reported on screen as version 2.
     *
     * That is the one number on this page a user checks against something they
     * did themselves: they confirmed a version, and this says which version was
     * judged. Being wrong about it is worse than omitting it.
     */
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.getByText(/Profile version 2/)).toBeInTheDocument();
    expect(screen.queryByText(/Profile version 3/)).not.toBeInTheDocument();
  });

  it("shows no database row id as provenance", async () => {
    /**
     * The footer used to carry "Requirements #7" — the `job_requirements`
     * primary key. A person cannot look that up, act on it, or tell two of them
     * apart, and a bare number in a line of facts reads as a fact.
     */
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.queryByText(/Requirements #/)).not.toBeInTheDocument();
    expect(screen.queryByText(/#\d+/)).not.toBeInTheDocument();
  });

  it("describes coverage as coverage and never as a prediction", async () => {
    // §12: this number is coverage of structured requirements by available
    // evidence. Nothing in the UI may call it a chance of anything.
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(
      screen.getByText(/not a prediction about an application/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/chance of/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/likelihood/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/probability/i)).not.toBeInTheDocument();
  });

  it("never tells the user they are unsuitable", async () => {
    // §13: the engine provides evidence; it does not reach a verdict about a
    // person. A "missing" requirement is labelled "Not found" for this reason.
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    for (const word of [/unsuitable/i, /not qualified/i, /rejected/i, /you failed/i]) {
      expect(screen.queryByText(word)).not.toBeInTheDocument();
    }
  });

  it("offers to match when the job has none yet", async () => {
    noExistingMatch();
    render(<JobMatchPage />);

    expect(await screen.findByText(/has not been matched yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Match my profile" })).toBeInTheDocument();
  });

  it("creates a match and shows the result", async () => {
    noExistingMatch();
    render(<JobMatchPage />);

    const button = await screen.findByRole("button", { name: "Match my profile" });
    await userEvent.click(button);

    expect(await screen.findByText("Requirement coverage")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
  });

  it("treats a repeated match as the same result rather than an error", async () => {
    // §18 over HTTP: the server answers 200 with the existing evaluation. The
    // UI must render it, not treat the different status code as a failure.
    noExistingMatch();
    server.use(
      http.post(`${BASE}/jobs/:id/matches`, () =>
        HttpResponse.json(matchDetail(), { status: 200 }),
      ),
    );
    render(<JobMatchPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Match my profile" }));

    expect(await screen.findByText("Requirement coverage")).toBeInTheDocument();
  });

  it("explains a job that has not been structured yet, and points at the job", async () => {
    noExistingMatch();
    server.use(
      http.post(`${BASE}/jobs/:id/matches`, () =>
        apiError(
          409,
          "This job has not been structured yet, so there are no requirements to match against.",
          "conflict",
        ),
      ),
    );
    render(<JobMatchPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Match my profile" }));

    expect(await screen.findByText(/has not been structured yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to job" })).toBeInTheDocument();
  });

  it("explains an unconfirmed profile, and points at the profile", async () => {
    noExistingMatch();
    server.use(
      http.post(`${BASE}/jobs/:id/matches`, () =>
        apiError(
          409,
          "This profile has no confirmed version yet. Confirm a version before matching.",
          "conflict",
        ),
      ),
    );
    render(<JobMatchPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Match my profile" }));

    expect(await screen.findByText(/Confirm a version before matching/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to profile" })).toBeInTheDocument();
  });

  it("shows an empty state for a job that does not exist", async () => {
    server.use(http.get(`${BASE}/jobs/:id`, () => apiError(404, "No such job.", "not_found")));
    render(<JobMatchPage />);

    expect(await screen.findByText("That job does not exist")).toBeInTheDocument();
  });

  it("reports a match it is not allowed to read rather than rendering nothing", async () => {
    server.use(
      http.get(`${BASE}/matches/:id`, () =>
        apiError(404, "That match does not exist.", "not_found"),
      ),
    );
    render(<JobMatchPage />);

    expect(await screen.findByText("That match does not exist.")).toBeInTheDocument();
  });

  it("renders a posting whose requirements are all unmet without claiming failure", async () => {
    server.use(
      http.get(`${BASE}/matches/:id`, () =>
        HttpResponse.json(
          matchDetail({
            requirement_results: [
              requirementResult({
                requirement_label: "Haskell",
                status: "missing",
                matched_value: null,
                evidence: [],
                explanation:
                  "No skill, project technology or description in this profile version mentions haskell. No qualifying evidence was found.",
              }),
            ],
          }),
        ),
      ),
    );
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");

    expect(screen.getByTestId("axis-required")).toHaveTextContent("0 of 1 matched");
    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(screen.getByText(/This posting states no preferred requirements/)).toBeInTheDocument();
  });

  it("renders a posting with no structured requirements at all", async () => {
    server.use(
      http.get(`${BASE}/matches/:id`, () =>
        HttpResponse.json(matchDetail({ requirement_results: [] })),
      ),
    );
    render(<JobMatchPage />);

    expect(
      await screen.findByText(/no structured requirements to check a profile against/),
    ).toBeInTheDocument();
  });
});

describe("JobMatchPage — which version's match is shown", () => {
  it("shows the selected version's match after Match, not the job's newest match", async () => {
    // The reported bug: the list is newest-first and a repeat POST answers with
    // the EXISTING row, so picking version 2 and pressing Match kept showing
    // version 3's result — the newest one — under a picker saying version 2.
    threeVersions();
    matchesFor({ id: 30, versionId: 13 }, { id: 25, versionId: 12 });
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/matches`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(
          matchDetail({ id: 25, profile_version_id: 12, profile_version_number: 2 }),
          { status: 200 },
        );
      }),
    );
    render(<JobMatchPage />);

    expect(await screen.findByText(/Profile version 3/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Match from"), "12");
    expect(await screen.findByText(/Profile version 2/)).toBeInTheDocument();
    expect(screen.queryByText(/Profile version 3/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Match again" }));
    expect(body).toEqual({ profile_version_id: 12 });
    expect(await screen.findByText(/Profile version 2/)).toBeInTheDocument();
    expect(screen.queryByText(/Profile version 3/)).not.toBeInTheDocument();
  });

  it("does not show an older version's match under the current version", async () => {
    // On load the picker said "Current confirmed profile (version 3)" while the
    // page showed a version 1 match. The current version has no match here.
    threeVersions();
    matchesFor({ id: 20, versionId: 11 });
    render(<JobMatchPage />);

    expect(
      await screen.findByText("Version 3 has not been matched against this job yet"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Requirement coverage")).not.toBeInTheDocument();
    // The older match is still one link away, named for what it is.
    expect(
      screen.getByRole("link", { name: "View the match from version 1" }),
    ).toHaveAttribute("href", "/jobs/1/match?match=20");
  });

  it("shows the match a link names, selects its version, and says it is not current", async () => {
    nav.search = new URLSearchParams("match=20");
    threeVersions();
    matchesFor({ id: 30, versionId: 13 }, { id: 20, versionId: 11 });
    render(<JobMatchPage />);

    expect(await screen.findByText(/Profile version 1/)).toBeInTheDocument();
    expect(screen.getByLabelText("Match from")).toHaveValue("11");
    expect(
      screen.getByText(/which is not your current version \(version 3\)/),
    ).toBeInTheDocument();
  });

  it("brings a linked match back after the user picked another version", async () => {
    // The page is not remounted when only the search params change, so the
    // earlier pick outlived the link: the picker stayed on version 2, and when
    // the URL already named the match, clicking did nothing at all.
    nav.search = new URLSearchParams("match=20");
    threeVersions();
    matchesFor({ id: 20, versionId: 11 });
    render(<JobMatchPage />);

    expect(await screen.findByText(/Profile version 1/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Match from"), "12");
    expect(
      await screen.findByText("Version 2 has not been matched against this job yet"),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("link", { name: "View the match from version 1" }));

    expect(await screen.findByText(/Profile version 1/)).toBeInTheDocument();
    expect(screen.getByLabelText("Match from")).toHaveValue("11");
  });

  it("carries the match's own version to the tailor page", async () => {
    render(<JobMatchPage />);

    expect(await screen.findByRole("link", { name: "Tailor resume" })).toHaveAttribute(
      "href",
      "/jobs/1/tailor?version=3&match=1",
    );
  });

  it("names the version explicitly when none is current", async () => {
    // A version confirmed with "make current" unticked: the picker says
    // "version 1", and omitting the id got a 409 saying nothing was confirmed.
    noExistingMatch();
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        HttpResponse.json([confirmedVersion({ id: 102, version_number: 1, is_current: false })]),
      ),
    );
    let body: unknown;
    server.use(
      http.post(`${BASE}/jobs/:id/matches`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(matchDetail(), { status: 201 });
      }),
    );
    render(<JobMatchPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Match my profile" }));
    expect(body).toEqual({ profile_version_id: 102 });
  });

  it("reports versions it could not read instead of calling the profile unconfirmed", async () => {
    server.use(
      http.get(`${BASE}/profile/versions`, () =>
        apiError(409, "The versions could not be listed.", "conflict"),
      ),
    );
    render(<JobMatchPage />);

    expect(await screen.findByText("The versions could not be listed.")).toBeInTheDocument();
    expect(screen.queryByText(/no confirmed profile version/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("does not add an empty state beside a match it could not read", async () => {
    server.use(
      http.get(`${BASE}/matches/:id`, () => apiError(409, "Unreadable.", "conflict")),
    );
    render(<JobMatchPage />);

    expect(await screen.findByText("Unreadable.")).toBeInTheDocument();
    expect(screen.queryByText(/has not been matched/)).not.toBeInTheDocument();
  });

  it("titles the document after the page and the job", async () => {
    render(<JobMatchPage />);

    await screen.findByText("Requirement coverage");
    expect(document.title).toBe("Match · Senior Backend Engineer · EasyApply");
  });
});
