import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { render, screen } from "@/test/render";
import { job, queuedRun, run } from "@/test/job-fixtures";
import { server } from "@/test/server";

import JobsPage from "./page";

const BASE = "/api/backend";

describe("JobsPage", () => {
  it("lists saved jobs with their processing state", async () => {
    render(<JobsPage />);

    expect(await screen.findByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Ready to review")).toBeInTheDocument();
    expect(screen.getByText(/Acme Corp/)).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been saved", async () => {
    server.use(http.get("/api/backend/jobs", () => HttpResponse.json([])));

    render(<JobsPage />);
    expect(await screen.findByText("No jobs yet")).toBeInTheDocument();
  });

  it("shows a queued job as queued, not as a failure or a blank state", async () => {
    server.use(
      http.get("/api/backend/jobs", () =>
        HttpResponse.json([job({ latest_run: queuedRun() })]),
      ),
    );

    render(<JobsPage />);
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("shows a job with no company as unknown rather than blank", async () => {
    server.use(
      http.get("/api/backend/jobs", () =>
        HttpResponse.json([job({ company_name: null, location_raw: null })]),
      ),
    );

    render(<JobsPage />);
    expect(await screen.findByText(/Unknown company/)).toBeInTheDocument();
  });

  it("links to the job's detail page", async () => {
    render(<JobsPage />);
    const link = (await screen.findByRole("link", {
      name: "View Senior Backend Engineer at Acme Corp",
    })) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/jobs/1");
  });

  it("gives every row's link a name of its own", async () => {
    // Read as a list of links — which is how a screen reader offers a page —
    // a column of identical "View"s says nothing about which posting each one
    // opens. The visible label stays short; the accessible name carries the row.
    server.use(
      http.get(`${BASE}/jobs`, () =>
        HttpResponse.json([
          job({ id: 1, job_title: "Senior Backend Engineer" }),
          job({ id: 2, job_title: "Platform Engineer" }),
          // One title at two companies: the title alone named both the same.
          job({ id: 3, job_title: "Platform Engineer", company_name: "Globex" }),
          job({ id: 4, job_title: "Data Engineer", company_name: null }),
        ]),
      ),
    );

    render(<JobsPage />);

    const links = await screen.findAllByRole("link", { name: /^View / });
    const names = links.map((link) => link.getAttribute("aria-label"));
    expect(names).toEqual([
      "View Senior Backend Engineer at Acme Corp",
      "View Platform Engineer at Acme Corp",
      "View Platform Engineer at Globex",
      "View Data Engineer",
    ]);
    expect(document.title).toBe("Jobs · EasyApply");
    expect(new Set(names).size).toBe(names.length);
  });

  it("offers no way to create a job — ingestion is the extension's job", async () => {
    render(<JobsPage />);
    await screen.findByText("Senior Backend Engineer");
    expect(screen.queryByRole("button", { name: /add/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});
