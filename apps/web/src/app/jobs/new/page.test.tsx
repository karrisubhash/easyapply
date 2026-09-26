import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jobDetail } from "@/test/job-fixtures";
import { render, screen, waitFor } from "@/test/render";
import { apiError, server, validationError } from "@/test/server";

import NewJobPage from "./page";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const BASE = "/api/backend";

/**
 * Installs the ingestion endpoint and returns the array the request bodies land
 * in. What this form is FOR is the payload — every assertion about "sends null,
 * not empty string" has to read the bytes the server actually received, not the
 * arguments a spy saw, because JSON.stringify is between the two.
 */
function captureCreate(respond: () => Response = created()) {
  const bodies: Record<string, unknown>[] = [];
  server.use(
    http.post(`${BASE}/jobs`, async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return respond();
    }),
  );
  return bodies;
}

function created(id = 42, status = 201) {
  return () => HttpResponse.json(jobDetail({ id }), { status });
}

// The page has no label-to-input association (see the accessibility note at the
// bottom of this file), so the controls are reached by their placeholders.
const TITLE = "Senior Backend Engineer";
const URL_FIELD = "https://example.com/jobs/123";
const COMPANY = "Acme Corp";
const LOCATION = "Berlin, Germany";
const EMPLOYMENT = "Full-time";
const WORKPLACE = "Hybrid";
const DESCRIPTION = "Paste the full posting text here.";

type User = ReturnType<typeof userEvent.setup>;

async function fillRequired(user: User) {
  await user.type(screen.getByPlaceholderText(TITLE), "Staff Platform Engineer");
  await user.type(
    screen.getByPlaceholderText(URL_FIELD),
    "https://boards.greenhouse.io/acme/jobs/9",
  );
  await user.type(screen.getByPlaceholderText(DESCRIPTION), "5+ years of Python.");
}

beforeEach(() => {
  push.mockClear();
});

describe("NewJobPage", () => {
  it("posts what was typed and opens the job the server created", async () => {
    const user = userEvent.setup();
    const bodies = captureCreate();

    render(<NewJobPage />);
    await fillRequired(user);
    await user.type(screen.getByPlaceholderText(COMPANY), "Acme Corp");
    await user.type(screen.getByPlaceholderText(LOCATION), "Berlin, Germany");
    await user.type(screen.getByPlaceholderText(EMPLOYMENT), "Full-time");
    await user.type(screen.getByPlaceholderText(WORKPLACE), "Hybrid");
    await user.selectOptions(screen.getByRole("combobox"), "greenhouse");

    await user.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    // Asserted whole rather than field by field: the extension and this form
    // must post the SAME shape, so an extra key invented here is as much a
    // divergence as a missing one.
    expect(bodies[0]).toEqual({
      source_type: "greenhouse",
      source_url: "https://boards.greenhouse.io/acme/jobs/9",
      job_title: "Staff Platform Engineer",
      company_name: "Acme Corp",
      location_raw: "Berlin, Germany",
      employment_type: "Full-time",
      workplace_type: "Hybrid",
      description_text: "5+ years of Python.",
    });

    await waitFor(() => expect(push).toHaveBeenCalledWith("/jobs/42"));
    // The first test in the file types eight fields a character at a time and
    // pays for the cold user-event setup: ~3s alone, past vitest's 5s default
    // under a full parallel run. Nothing here waits on purpose, so the
    // allowance is for the machine, not for the page.
  }, 15_000);

  it("sends an untouched optional field as null, never as an empty string", async () => {
    const user = userEvent.setup();
    const bodies = captureCreate();

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    // "" is a value the server would have to assign a meaning to — is the
    // company named as nothing, or not named at all? `null` already says "not
    // stated", and the normalizer downstream depends on the difference.
    expect(bodies[0]).toEqual({
      source_type: "other",
      source_url: "https://boards.greenhouse.io/acme/jobs/9",
      job_title: "Staff Platform Engineer",
      company_name: null,
      location_raw: null,
      employment_type: null,
      workplace_type: null,
      description_text: "5+ years of Python.",
    });
    expect(Object.values(bodies[0])).not.toContain("");
  });

  it("treats a field of only whitespace as not stated", async () => {
    const user = userEvent.setup();
    const bodies = captureCreate();

    render(<NewJobPage />);
    await fillRequired(user);
    // Spaces are what a paste off a careers page leaves behind. They are not
    // an answer to "what company is this?", so they must not reach the server
    // as one.
    await user.type(screen.getByPlaceholderText(COMPANY), "   ");
    await user.type(screen.getByPlaceholderText(LOCATION), "  Berlin, Germany  ");
    await user.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].company_name).toBeNull();
    expect(bodies[0].location_raw).toBe("Berlin, Germany");
  });

  // --- the states this surface owes the user -----------------------------------

  it("says it is saving and refuses a second submit while the first is in flight", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const inFlight = new Promise<void>((resolve) => {
      release = resolve;
    });
    // A gate rather than a timer: the pending state has to be observable
    // without the test depending on how long anything takes.
    const bodies: unknown[] = [];
    server.use(
      http.post(`${BASE}/jobs`, async ({ request }) => {
        bodies.push(await request.json());
        await inFlight;
        return HttpResponse.json(jobDetail({ id: 42 }), { status: 201 });
      }),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    const saving = await screen.findByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    // Double-posting an ingestion is how you get two runs for one posting.
    await user.click(saving);
    expect(bodies).toHaveLength(1);

    release();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/jobs/42"));
  });

  it("shows the server's field-level issues rather than one generic failure", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${BASE}/jobs`, () =>
        validationError(["body", "source_url"], "URL must use http or https."),
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    const alert = await screen.findByRole("alert");
    // The page deliberately re-implements none of the server's validation, so
    // a 422 is the ONLY way the user learns which field was wrong. Collapsing
    // it to "something went wrong" would leave them with no next move.
    expect(alert).toHaveTextContent("Some of this data was rejected");
    expect(alert).toHaveTextContent("source_url");
    expect(alert).toHaveTextContent("URL must use http or https.");
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("names every rejected field when the server rejects more than one", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${BASE}/jobs`, () =>
        HttpResponse.json(
          {
            detail: [
              { loc: ["body", "source_url"], msg: "URL must use http or https.", type: "value_error" },
              { loc: ["body", "job_title"], msg: "String is too long.", type: "value_error" },
            ],
          },
          { status: 422 },
        ),
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("URL must use http or https.");
    expect(alert).toHaveTextContent("String is too long.");
  });

  it("says the posting was already saved, and that nothing typed here was kept", async () => {
    const user = userEvent.setup();
    // Deduplication answers 200 with the existing job, UNCHANGED, rather than
    // 201 with a new one. It is not a failure — but opening that job as if this
    // save had worked hid that the description typed here was thrown away, and
    // a job saved without one could never be fixed by saving it again.
    const bodies = captureCreate(() =>
      HttpResponse.json(jobDetail({ id: 7, description_text: null }), { status: 200 }),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    expect(await screen.findByText("You already saved this posting")).toBeInTheDocument();
    expect(screen.getByText(/nothing you entered here was saved/)).toBeInTheDocument();
    expect(screen.getByText(/That job has no description/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the saved job" })).toHaveAttribute(
      "href",
      "/jobs/7",
    );
    expect(push).not.toHaveBeenCalled();
    expect(bodies).toHaveLength(1);
    // What was typed survives.
    expect(screen.getByPlaceholderText(TITLE)).toHaveValue("Staff Platform Engineer");
  });

  it("opens the job when the one already saved is exactly what was entered", async () => {
    // A retried save whose first answer was lost: the server already holds
    // this very job, so "nothing you entered here was saved" would be false.
    const user = userEvent.setup();
    captureCreate(() =>
      HttpResponse.json(
        jobDetail({
          id: 7,
          job_title: "Staff Platform Engineer",
          company_name: null,
          description_text: "5+ years of Python.",
        }),
        { status: 200 },
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/jobs/7"));
    expect(screen.queryByText("You already saved this posting")).not.toBeInTheDocument();
  });

  it("says why an empty required field stopped the save, on the field", async () => {
    const user = userEvent.setup();
    captureCreate();

    render(<NewJobPage />);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    const title = screen.getByPlaceholderText(TITLE);
    await waitFor(() => expect(title).toHaveAccessibleDescription("Job title is required."));
    expect(screen.getByPlaceholderText(URL_FIELD)).toHaveAccessibleDescription(
      "Posting URL is required.",
    );
    expect(screen.getByPlaceholderText(DESCRIPTION)).toHaveAccessibleDescription(
      /Description is required\./,
    );
    expect(document.title).toBe("Add a job · EasyApply");
  });

  it("explains a refused save in the API's own words and stays on the form", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${BASE}/jobs`, () =>
        apiError(409, "This posting was already saved under a different account.", "conflict"),
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That conflicts with the current state");
    expect(alert).toHaveTextContent("This posting was already saved under a different account.");
    // Navigating to a job the server refused to create would be a link to
    // nothing.
    expect(push).not.toHaveBeenCalled();
    // And what was typed survives, so the user can fix it rather than retype it.
    expect(screen.getByPlaceholderText(TITLE)).toHaveValue("Staff Platform Engineer");
  });

  it("shows a reachable sentence and nothing from inside the server when the API is down", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(`${BASE}/jobs`, () =>
        apiError(502, "The API is not responding.", "upstream_unavailable"),
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    expect(await screen.findByText("The API is unreachable")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Traceback|INSERT |psycopg|"detail"|\{"/);
  });

  it("renders an HTML error page as a failure rather than a parser crash", async () => {
    const user = userEvent.setup();
    // Anything that answers before the API — a proxy, a gateway — replies with
    // HTML. The form must not put "Unexpected token <" in front of the user.
    server.use(
      http.post(`${BASE}/jobs`, () =>
        HttpResponse.text("<html><body>502 Bad Gateway</body></html>", { status: 502 }),
      ),
    );

    render(<NewJobPage />);
    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    expect(await screen.findByText("The API is unreachable")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Unexpected token|JSON\.parse|<html>/);
  });

  // --- the required fields ------------------------------------------------------

  it("does not post an empty form", async () => {
    const user = userEvent.setup();
    const bodies = captureCreate();

    render(<NewJobPage />);
    await user.click(screen.getByRole("button", { name: "Save job" }));

    // A job with no title and no link is not a job; ingesting one would create
    // a row nothing can ever match or tailor against.
    await waitFor(() =>
      expect(screen.getByPlaceholderText(TITLE)).toHaveAttribute("aria-invalid", "true"),
    );
    expect(screen.getByPlaceholderText(URL_FIELD)).toHaveAttribute("aria-invalid", "true");
    expect(bodies).toHaveLength(0);
    expect(push).not.toHaveBeenCalled();
  });

  it("does not post when only one of the two required fields is filled", async () => {
    const user = userEvent.setup();
    const bodies = captureCreate();

    render(<NewJobPage />);
    await user.type(screen.getByPlaceholderText(TITLE), "Staff Platform Engineer");
    await user.click(screen.getByRole("button", { name: "Save job" }));

    await waitFor(() =>
      expect(screen.getByPlaceholderText(URL_FIELD)).toHaveAttribute("aria-invalid", "true"),
    );
    expect(screen.getByPlaceholderText(TITLE)).toHaveAttribute("aria-invalid", "false");
    expect(bodies).toHaveLength(0);
  });

  it("marks exactly the three fields a usable job cannot do without", async () => {
    render(<NewJobPage />);

    // A title and a link identify the posting. The description is what the
    // requirements are read out of — and it cannot be pasted in later: saving
    // the same posting again returns the stored job unchanged.
    expect(screen.getByText("Job title").textContent).toContain("*");
    expect(screen.getByText("Posting URL").textContent).toContain("*");
    expect(screen.getByText("Description").textContent).toContain("*");
    for (const optional of ["Company", "Location", "Employment type"]) {
      expect(screen.getByText(optional).textContent).not.toContain("*");
    }
  });

  it("offers every source the ingestion schema accepts and defaults to none of them", async () => {
    render(<NewJobPage />);

    const select = screen.getByRole("combobox");
    // "other" rather than a guess: the form must not attribute a posting to
    // LinkedIn because LinkedIn happened to sort first.
    expect(select).toHaveValue("other");
    expect(
      Array.from(select.querySelectorAll("option")).map((option) => option.value),
    ).toEqual([
      "linkedin",
      "indeed",
      "greenhouse",
      "lever",
      "workday",
      "company_careers",
      "other",
    ]);
  });

  // --- the way out --------------------------------------------------------------

  it("leads back to a page that exists from both exits", async () => {
    render(<NewJobPage />);

    for (const name of ["Back to jobs", "Cancel"]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", "/jobs");
    }
  });
});

/**
 * NOT TESTED — real accessibility bug, reported rather than worked around.
 *
 * `Field` renders `<Label>{label}</Label>` as a SIBLING of the control, with no
 * `htmlFor`, no `id` on the input and no `aria-labelledby`, so not one of the
 * eight controls has an accessible name. `getByLabelText("Job title")` finds
 * nothing, clicking a label does not focus its input, and a screen reader
 * announces eight unnamed text boxes. The asterisk on the two required fields
 * is in the label text only — no `required` or `aria-required` on the input —
 * so it is invisible to assistive technology for the same reason.
 *
 * The tests above reach the controls by placeholder, which is a stand-in for a
 * name and not one: it disappears the moment the field has a value. Once
 * `Field` generates an id and passes `htmlFor`, these queries should become
 * `getByLabelText`.
 */

/**
 * Regression: every control on this form was unnamed.
 *
 * `Field` rendered its `<Label>` as a SIBLING of the control, with no `htmlFor`
 * and no id on the input. A screen reader announced eight unlabelled text
 * boxes, and clicking a label focused nothing. These assertions are written
 * with `getByLabelText` on purpose — the query only resolves when the
 * association is real, so the test cannot pass by accident.
 */
describe("NewJobPage — the form is usable without sight of it", () => {
  it.each([
    "Job title",
    "Company",
    "Posting URL",
    "Where you found it",
    "Location",
    "Employment type",
    "Workplace type",
    "Description",
  ])("names the %s control", (label) => {
    render(<NewJobPage />);

    expect(screen.getByLabelText(new RegExp(`^${label}`, "i"))).toBeInTheDocument();
  });

  it("focuses the control when its label is clicked", async () => {
    const user = userEvent.setup();
    render(<NewJobPage />);

    await user.click(screen.getByText("Company"));

    expect(screen.getByLabelText(/^Company/i)).toHaveFocus();
  });

  it("tells assistive technology which fields are required, not just the eye", async () => {
    // The asterisk is a sighted convention that a screen reader does not read.
    // On its own it told some users a field was required and everyone else
    // nothing — until after a failed submit, when aria-invalid appeared.
    render(<NewJobPage />);

    expect(screen.getByLabelText(/^Job title/i)).toBeRequired();
    expect(screen.getByLabelText(/^Posting URL/i)).toBeRequired();
    expect(screen.getByLabelText(/^Company/i)).not.toBeRequired();
    expect(screen.getByLabelText(/^Description/i)).toBeRequired();
  });

  it("does not read the required asterisk out as part of the field name", () => {
    render(<NewJobPage />);

    // Asserted on the mechanism rather than through getByLabelText, which
    // matches a label's raw textContent and does not implement the accessible
    // name algorithm — it would see "Job title *" either way and prove nothing.
    const asterisks = Array.from(document.querySelectorAll("label span"));

    expect(asterisks.length).toBeGreaterThan(0);
    expect(asterisks.every((span) => span.getAttribute("aria-hidden") === "true")).toBe(
      true,
    );
  });

  it("gives every control a distinct name", () => {
    render(<NewJobPage />);

    const named = Array.from(
      document.querySelectorAll("input, select, textarea"),
    ).map((control) => {
      const id = control.getAttribute("id");
      return id ? document.querySelector(`label[for="${id}"]`)?.textContent : null;
    });

    expect(named).toHaveLength(8);
    expect(named.every(Boolean)).toBe(true);
    expect(new Set(named).size).toBe(8);
  });
});
