import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { render, screen } from "@/test/render";
import { jobDetail, jobRequirementsResult, run } from "@/test/job-fixtures";
import { apiError, server } from "@/test/server";

import JobDetailPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
}));

describe("JobDetailPage", () => {
  it("shows job metadata and the description as originally captured", async () => {
    render(<JobDetailPage />);

    expect(await screen.findByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText(/Acme Corp/)).toBeInTheDocument();
    expect(screen.getByText(/5\+ years of Python experience/)).toBeInTheDocument();
  });

  it("shows the structured requirements with their citations, split by required/preferred", async () => {
    render(<JobDetailPage />);

    await screen.findByText("Senior Backend Engineer");

    // Required and preferred technologies are shown as distinct groups —
    // the split the backend schema itself makes, not merged back together.
    // Awaited: the requirements query resolves separately from the job
    // itself, once `isDone` gates it open.
    expect((await screen.findAllByText("Python"))[0]).toBeInTheDocument();
    expect(screen.getAllByText("PostgreSQL")[0]).toBeInTheDocument();
    expect(screen.getAllByText("Kubernetes")[0]).toBeInTheDocument();

    const citations = await screen.findAllByTestId("citation");
    expect(citations.length).toBeGreaterThan(0);
    expect(citations[0]).toHaveTextContent("5+ years of Python experience");
    // Confidence is disclosed inline, not only on hover — the same posture
    // this UI already takes for an unverified citation.
    expect(citations[0]).toHaveTextContent("50% confidence");
  });

  it("flags a citation the server could not verify", async () => {
    server.use(
      http.get("/api/backend/jobs/:id/requirements", () =>
        HttpResponse.json(
          jobRequirementsResult({
            requirements: {
              structurer: "deterministic",
              provider: null,
              model: null,
              prompt_version: null,
              schema_version: "1",
              created_at: "2026-09-19T09:00:02Z",
              candidate: {
                required_skills: [],
                preferred_skills: [],
                required_experience: [],
                preferred_experience: [],
                education: [],
                certifications: [],
                responsibilities: [],
                technologies: [
                  {
                    name: "Rust",
                    category: null,
                    is_required: true,
                    provenance: [
                      {
                        source_section: "required",
                        quote: "Must know Rust",
                        confidence: 0.5,
                        verified: false,
                      },
                    ],
                  },
                ],
                qualifications: [],
              },
            },
          }),
        ),
      ),
    );

    render(<JobDetailPage />);
    expect(await screen.findByText(/was not found in the description/)).toBeInTheDocument();
  });

  it("shows an empty state for a job that does not exist", async () => {
    server.use(
      http.get("/api/backend/jobs/:id", () => apiError(404, "That job does not exist.", "not_found")),
    );

    render(<JobDetailPage />);
    expect(await screen.findByText("That job does not exist")).toBeInTheDocument();
  });

  it("explains a failure safely, without exposing internal detail", async () => {
    server.use(
      http.get("/api/backend/jobs/:id", () =>
        HttpResponse.json(
          jobDetail({
            latest_run: run({
              status: "failed",
              failed_stage: "structure",
              error_code: "unexpected_error",
              error_message: "Traceback: /var/lib/job-worker/internal-path:42",
            }),
          }),
        ),
      ),
    );

    render(<JobDetailPage />);

    expect(
      await screen.findByText("Processing failed while reading the description"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Trying again may help/)).toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
    expect(screen.queryByText(/var\/lib/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps showing the last successful reading after a reprocess fails", async () => {
    server.use(
      http.get("/api/backend/jobs/:id", () =>
        HttpResponse.json(
          jobDetail({
            latest_run: run({ id: 2, attempt: 2, status: "failed", error_code: "unexpected_error" }),
          }),
        ),
      ),
      http.get("/api/backend/jobs/:id/requirements", () =>
        HttpResponse.json(jobRequirementsResult({ run: run({ id: 1 }) })),
      ),
    );

    render(<JobDetailPage />);

    expect(await screen.findByText("Showing the last reading that succeeded")).toBeInTheDocument();
    // The reading matching and tailoring still act on is on the page, and so
    // are the actions that use it.
    expect(await screen.findByText(/Interpreted by/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Match my profile" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tailor resume" })).toBeInTheDocument();
    // One retry control, not two.
    expect(screen.queryByRole("button", { name: "Reprocess" })).not.toBeInTheDocument();
  });

  it("retries on request", async () => {
    let retried = false;
    server.use(
      http.get("/api/backend/jobs/:id", () =>
        HttpResponse.json(jobDetail({ latest_run: run({ status: "failed", failed_stage: "structure", error_code: "unexpected_error" }) })),
      ),
      http.post("/api/backend/jobs/:id/reprocess", () => {
        retried = true;
        return HttpResponse.json(jobDetail());
      }),
    );

    render(<JobDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));

    await vi.waitFor(() => expect(retried).toBe(true));
  });

  it("shows a queued job as queued", async () => {
    server.use(
      http.get("/api/backend/jobs/:id", () =>
        HttpResponse.json(jobDetail({ latest_run: run({ status: "queued", started_at: null, finished_at: null }) })),
      ),
    );

    render(<JobDetailPage />);
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("shows a job with no captured description honestly", async () => {
    server.use(
      http.get("/api/backend/jobs/:id", () =>
        HttpResponse.json(jobDetail({ description_text: null })),
      ),
    );

    render(<JobDetailPage />);
    expect(await screen.findByText(/No description was captured/)).toBeInTheDocument();
  });

  it("links to the original posting", async () => {
    render(<JobDetailPage />);
    const link = (await screen.findByRole("link", {
      name: "View original",
    })) as HTMLAnchorElement;
    expect(link.href).toBe("https://www.linkedin.com/jobs/view/123456");
    expect(link.target).toBe("_blank");
  });

  it("reports a refused Reprocess on a completed job instead of failing silently", async () => {
    server.use(
      http.post("/api/backend/jobs/:id/reprocess", () =>
        apiError(409, "This job is already being processed.", "conflict"),
      ),
    );
    render(<JobDetailPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Reprocess" }));

    expect(await screen.findByText("This job is already being processed.")).toBeInTheDocument();
  });

  it("keeps a page heading while it cannot find the job", async () => {
    server.use(http.get("/api/backend/jobs/:id", () => apiError(404, "No such job.", "not_found")));
    render(<JobDetailPage />);

    expect(await screen.findByText("That job does not exist")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Job" })).toBeInTheDocument();
  });

  it("titles the document after the job", async () => {
    render(<JobDetailPage />);

    await screen.findByText("Senior Backend Engineer");
    expect(document.title).toBe("Senior Backend Engineer · Acme Corp · EasyApply");
  });
});
