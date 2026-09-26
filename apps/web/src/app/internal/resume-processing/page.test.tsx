import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { render, screen, within } from "@/test/render";
import {
  attentionList,
  operatorDocument,
  operatorDocumentDetail,
} from "@/test/operations-fixtures";
import { apiError, server } from "@/test/server";

import ResumeProcessingOperations from "./page";

const ATTENTION = "/api/backend/internal/resume-processing/attention";

describe("ResumeProcessingOperations", () => {
  it("lists what needs attention, with the failure code and attempt count", async () => {
    render(<ResumeProcessingOperations />);

    // Scoped to the table: the same state label also appears in the counts row
    // above it, which is the point of the counts row.
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Exhausted")).toBeInTheDocument();
    expect(within(table).getByText("ai_rate_limited")).toBeInTheDocument();
    expect(within(table).getByText("3/3")).toBeInTheDocument();
  });

  it("distinguishes exhausted from a permanent first failure", async () => {
    // Both are "failed and not retrying"; sending an operator to hunt an outage
    // when the file is simply unreadable is the failure this view must avoid.
    server.use(
      http.get(ATTENTION, () =>
        HttpResponse.json(
          attentionList({
            documents: [
              operatorDocument({ document_id: 1, state: "exhausted" }),
              operatorDocument({
                document_id: 2,
                state: "failed_permanent",
                error_code: "malformed_pdf",
                failure_classification: "permanent",
                attempts_used: 1,
              }),
            ],
            counts: { exhausted: 1, failed_permanent: 1 },
          }),
        ),
      ),
    );

    render(<ResumeProcessingOperations />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Exhausted")).toBeInTheDocument();
    expect(within(table).getByText("Permanent failure")).toBeInTheDocument();
    expect(within(table).getByText("malformed_pdf")).toBeInTheDocument();
  });

  it("shows a retry-scheduled run as waiting rather than as a problem", async () => {
    server.use(
      http.get(ATTENTION, () =>
        HttpResponse.json(
          attentionList({
            documents: [
              operatorDocument({
                state: "waiting_retry",
                status: "uploaded",
                failed_stage: null,
                error_code: null,
                failure_classification: null,
                next_retry_at: "2026-09-18T09:30:00Z",
                attempts_used: 2,
              }),
            ],
            counts: { waiting_retry: 1 },
          }),
        ),
      ),
    );

    render(<ResumeProcessingOperations />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Waiting to retry")).toBeInTheDocument();
    // The scheduled time is shown, so a designed delay does not read as a hang.
    expect(within(table).getAllByText(/Sep 18, 2026/).length).toBeGreaterThan(0);
  });

  it("shows a run being worked on as held", async () => {
    server.use(
      http.get(ATTENTION, () =>
        HttpResponse.json(
          attentionList({
            documents: [
              operatorDocument({
                state: "processing",
                status: "structuring",
                failed_stage: null,
                error_code: null,
                leased: true,
              }),
            ],
            counts: { processing: 1 },
          }),
        ),
      ),
    );

    render(<ResumeProcessingOperations />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Processing")).toBeInTheDocument();
    expect(within(table).getByText(/held/)).toBeInTheDocument();
  });

  it("counts every state even while the list is filtered", async () => {
    render(<ResumeProcessingOperations />);

    // One exhausted document is listed, but the counts still say four
    // completed and two waiting — a single bad document versus an outage.
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Exhausted")).toBeInTheDocument();

    const completed = screen.getByTitle(/Produced a draft/);
    expect(completed).toHaveTextContent("4");
    const waiting = screen.getByTitle(/Backing off/);
    expect(waiting).toHaveTextContent("2");
  });

  it("can switch to showing everything", async () => {
    render(<ResumeProcessingOperations />);
    expect(
      await screen.findByText("Showing only what needs attention."),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show everything" }));

    expect(await screen.findByText("Showing every document.")).toBeInTheDocument();
  });

  it("opens one document's attempts and keeps every one of them", async () => {
    render(<ResumeProcessingOperations />);
    await userEvent.click(await screen.findByRole("button", { name: "Attempts" }));

    expect(await screen.findByText("Document 1 — attempts")).toBeInTheDocument();
    // Attempt 1 is still there beside attempt 2: history is the diagnosis.
    expect(screen.getByText("ai_timeout")).toBeInTheDocument();
    expect(screen.getAllByText(/anthropic/).length).toBeGreaterThan(0);
  });

  it("says so plainly when nothing needs attention", async () => {
    server.use(
      http.get(ATTENTION, () =>
        HttpResponse.json(attentionList({ documents: [], counts: { completed: 9 } })),
      ),
    );

    render(<ResumeProcessingOperations />);
    expect(await screen.findByText("Nothing needs attention.")).toBeInTheDocument();
  });

  it("reports a refused operator token instead of an empty table", async () => {
    server.use(
      http.get(ATTENTION, () =>
        apiError(403, "A valid operator token is required.", "forbidden"),
      ),
    );

    render(<ResumeProcessingOperations />);
    expect(
      await screen.findByText("A valid operator token is required."),
    ).toBeInTheDocument();
  });

  it("renders nothing that could carry resume content", async () => {
    // The API cannot send these fields. This asserts the page never invents a
    // place to put them either.
    server.use(
      http.get(ATTENTION, () => HttpResponse.json(attentionList())),
      http.get("/api/backend/internal/resume-processing/documents/:id", () =>
        HttpResponse.json(operatorDocumentDetail()),
      ),
    );

    render(<ResumeProcessingOperations />);
    await userEvent.click(await screen.findByRole("button", { name: "Attempts" }));
    await screen.findByText("Document 1 — attempts");

    const body = document.body.textContent ?? "";
    for (const forbidden of [
      "original_filename",
      "storage_key",
      "content_sha256",
      "payload",
      "provenance",
      "@example.com",
      "var/resume-storage",
    ]) {
      expect(body).not.toContain(forbidden);
    }
  });

  it("identifies a user by id only, never by address", async () => {
    render(<ResumeProcessingOperations />);
    const table = await screen.findByRole("table");

    expect(within(table).getByText("42")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/@/);
  });
});
