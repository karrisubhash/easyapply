import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { confirmedVersion } from "@/test/fixtures";
import { render, screen } from "@/test/render";
import {
  processingResult,
  queuedRun,
  waitingRetryRun,
  resumeDetail,
  run,
  structuredResult,
} from "@/test/resume-fixtures";
import { apiError, server } from "@/test/server";

import ResumeDetailPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ documentId: "1" }),
}));

describe("ResumeDetailPage", () => {
  it("shows the structured reading with its citations", async () => {
    render(<ResumeDetailPage />);

    expect(await screen.findByText("AI Developer Intern")).toBeInTheDocument();
    expect(screen.getByText(/AI Developer Intern, APTRANSCO/)).toBeInTheDocument();
    expect(screen.getAllByTestId("citation").length).toBeGreaterThan(0);
  });

  it("shows a date in the words the resume used, not a normalized one", async () => {
    /** Rendering "1 May 2024" would put back the false precision the
        pipeline exists to avoid. */
    render(<ResumeDetailPage />);

    expect(await screen.findByText("May 2024")).toBeInTheDocument();
    expect(screen.queryByText("2024-05-01")).not.toBeInTheDocument();
  });

  it("links to the draft version it produced", async () => {
    render(<ResumeDetailPage />);

    expect(await screen.findByText("Draft version 3 is ready")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit the draft" })).toHaveAttribute(
      "href",
      "/profile/versions/3/edit",
    );
    expect(screen.getByRole("link", { name: "Review and confirm" })).toHaveAttribute(
      "href",
      "/profile/versions/3",
    );
  });

  it("says nothing is in the profile until it is confirmed", async () => {
    render(<ResumeDetailPage />);
    expect(await screen.findByText(/Nothing here is part of your profile yet/)).toBeInTheDocument();
  });

  it("flags a citation the server could not verify", async () => {
    server.use(
      http.get("/api/backend/resumes/:id/result", () =>
        HttpResponse.json(
          processingResult({
            result: structuredResult({
              candidate: {
                experiences: [],
                education: [],
                projects: [],
                certifications: [],
                skills: [
                  {
                    name: "Fabricated",
                    category: null,
                    proficiency: null,
                    years_of_experience: null,
                    provenance: [
                      { page: 1, quote: "never written", confidence: 0.9, verified: false },
                    ],
                  },
                ],
              },
            }),
          }),
        ),
      ),
    );

    render(<ResumeDetailPage />);
    expect(
      await screen.findByText(/this quote was not found on that page/),
    ).toBeInTheDocument();
  });

  it("explains items that could not be added to the draft", async () => {
    server.use(
      http.get("/api/backend/resumes/:id/result", () =>
        HttpResponse.json(
          processingResult({
            result: structuredResult({
              unmapped: [
                {
                  section: "experiences",
                  reason: "The resume did not give a start date.",
                  summary: "Freelance work",
                },
              ],
            }),
          }),
        ),
      ),
    );

    render(<ResumeDetailPage />);

    expect(
      await screen.findByText("Some items could not be added to the draft"),
    ).toBeInTheDocument();
    expect(screen.getByText("Freelance work")).toBeInTheDocument();
    expect(screen.getByText(/did not give a start date/)).toBeInTheDocument();
  });

  it("explains a failure the document causes and does not offer a retry", async () => {
    // A password-protected PDF fails the same way every time, whatever anyone
    // changes. A button that said otherwise would be a lie dressed as help.
    const failed = run({
      status: "failed",
      failed_stage: "extract",
      error_code: "document_encrypted",
      profile_version_id: null,
    });
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: failed })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText("Processing failed while reading the text")).toBeInTheDocument();
    expect(screen.getByText(/password protected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByText(/fail the same way/)).toBeInTheDocument();
  });

  it("offers a retry for a failure the deployment caused", async () => {
    // No OCR engine is not the document's fault: once one is installed the
    // same file reads, and a re-upload of the same bytes resolves to this
    // same document — so the server offers a retry, and so does the page.
    const failed = run({
      status: "failed",
      failed_stage: "ocr",
      error_code: "ocr_unavailable",
      profile_version_id: null,
    });
    expect(failed.can_retry).toBe(true);
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: failed })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText("Processing failed while scanning the pages")).toBeInTheDocument();
    expect(screen.getByText(/scanned document/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText(/fail the same way/)).not.toBeInTheDocument();
  });

  it("offers a retry for a failure that could go differently", async () => {
    const failed = run({
      status: "failed",
      failed_stage: "validate",
      error_code: "stored_object_missing",
      profile_version_id: null,
    });
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: failed })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText(/Trying again may help/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("retries on request", async () => {
    let retried = false;
    const failed = run({
      status: "failed",
      failed_stage: "structure",
      error_code: "ai_timeout",
      profile_version_id: null,
    });
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: failed })),
      ),
      http.post("/api/backend/resumes/:id/reprocess", () => {
        retried = true;
        return HttpResponse.json(resumeDetail());
      }),
    );

    render(<ResumeDetailPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));

    await vi.waitFor(() => expect(retried).toBe(true));
  });

  it("never shows internal exception text or a filesystem path", async () => {
    // The message column is written from a fixed vocabulary for exactly this
    // reason; the UI must not undo that by rendering whatever arrives.
    const failed = run({
      status: "failed",
      failed_stage: "extract",
      error_code: "malformed_pdf",
      error_message: "Traceback: /var/resume-storage/resumes/16/abc.pdf could not be read",
      profile_version_id: null,
    });
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: failed })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText(/It may be damaged/)).toBeInTheDocument();
    expect(screen.queryByText(/var\/resume-storage/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument();
  });

  it("shows a queued run as waiting for a worker", async () => {
    // Queued is a state a user genuinely sits in now, not a millisecond
    // between the request and the response.
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: queuedRun() })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText("Queued…")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for a worker/)).toBeInTheDocument();
  });

  it("says a scheduled retry is waiting, not that it is queued", async () => {
    // A backoff and a queue are the same `status`. A user told "Queued" for two
    // minutes reasonably concludes the thing is broken.
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: waitingRetryRun() })),
      ),
    );

    render(<ResumeDetailPage />);

    expect(await screen.findByText("Waiting to try again…")).toBeInTheDocument();
    expect(screen.getByText(/temporary problem/)).toBeInTheDocument();
    expect(screen.getByText(/do not need to do anything/)).toBeInTheDocument();
    expect(screen.queryByText("Queued…")).not.toBeInTheDocument();
  });

  it("says which attempt is running when it is not the first", async () => {
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(
          resumeDetail({ latest_run: run({ status: "structuring", attempt: 2 }) }),
        ),
      ),
    );

    render(<ResumeDetailPage />);
    expect(await screen.findByText("Attempt 2.")).toBeInTheDocument();
  });

  it("shows a processing state while work is in flight", async () => {
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        HttpResponse.json(resumeDetail({ latest_run: run({ status: "extracting" }) })),
      ),
    );

    render(<ResumeDetailPage />);
    expect(await screen.findByText("Reading the text…")).toBeInTheDocument();
  });

  it("shows an empty state for a resume that does not exist", async () => {
    server.use(
      http.get("/api/backend/resumes/:id", () =>
        apiError(404, "That resume does not exist.", "not_found"),
      ),
    );

    render(<ResumeDetailPage />);
    expect(await screen.findByText("That resume does not exist")).toBeInTheDocument();
  });

  it("says the draft was confirmed once it has been, and stops offering to edit it", async () => {
    server.use(
      http.get("/api/backend/profile/versions", () =>
        HttpResponse.json([confirmedVersion({ id: 300, version_number: 3, is_current: true })]),
      ),
    );
    render(<ResumeDetailPage />);

    expect(await screen.findByText("Confirmed as version 3")).toBeInTheDocument();
    expect(screen.queryByText("Draft version 3 is ready")).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing here is part of your profile yet/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit the draft" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View version 3" })).toHaveAttribute(
      "href",
      "/profile/versions/3",
    );
  });

  it("keeps a page heading while it cannot find the resume", async () => {
    server.use(
      http.get("/api/backend/resumes/:id", () => apiError(404, "No such resume.", "not_found")),
    );
    render(<ResumeDetailPage />);

    expect(await screen.findByText("That resume does not exist")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Resume" })).toBeInTheDocument();
  });
});
