import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { render, screen, waitFor } from "@/test/render";
import { queuedRun, resumeDocument, run } from "@/test/resume-fixtures";
import { apiError, server } from "@/test/server";

import ResumePage from "./page";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...args: unknown[]) => push(...args) }),
  useParams: () => ({}),
}));

function pdf(name = "resume.pdf") {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, {
    type: "application/pdf",
  });
}

describe("ResumePage", () => {
  it("lists uploaded resumes with their processing state", async () => {
    render(<ResumePage />);

    expect(await screen.findByText("resume.pdf")).toBeInTheDocument();
    expect(screen.getByText("Ready to review")).toBeInTheDocument();
  });

  it("shows a freshly uploaded resume as queued", async () => {
    // The upload endpoint returns before any work has been done, so this is
    // what a user sees immediately after choosing a file.
    server.use(
      http.get("/api/backend/resumes", () =>
        HttpResponse.json([resumeDocument({ latest_run: queuedRun() })]),
      ),
    );

    render(<ResumePage />);

    expect(await screen.findByText("resume.pdf")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been uploaded", async () => {
    server.use(http.get("/api/backend/resumes", () => HttpResponse.json([])));

    render(<ResumePage />);
    expect(await screen.findByText("No resumes yet")).toBeInTheDocument();
  });

  it("uploads a file and navigates to it", async () => {
    render(<ResumePage />);

    await userEvent.upload(screen.getByLabelText("Resume file"), pdf());

    await waitFor(() => expect(push).toHaveBeenCalledWith("/resume/1"));
  });

  it("reports a rejected file type using the server's own message", async () => {
    server.use(
      http.post("/api/backend/resumes", () =>
        apiError(422, "Only PDF and DOCX resumes are supported.", "unsupported_file_type"),
      ),
    );

    render(<ResumePage />);
    await userEvent.upload(screen.getByLabelText("Resume file"), pdf("notes.txt"));

    expect(
      await screen.findByText("Only PDF and DOCX resumes are supported."),
    ).toBeInTheDocument();
    expect(screen.getByText(/unsupported_file_type/)).toBeInTheDocument();
  });

  it("clears the file input so the same file can be picked again", async () => {
    // A real file input fires `change` only when the selection differs from what
    // it already holds, so a failed upload cannot be retried with the same file
    // unless the input is cleared. jsdom does not model that rule, so the test
    // asserts the property the browser depends on rather than the symptom.
    server.use(
      http.post("/api/backend/resumes", () =>
        apiError(502, "The EasyApply API is unreachable.", "api_unreachable"),
      ),
    );

    render(<ResumePage />);
    const input = screen.getByLabelText("Resume file") as HTMLInputElement;
    await userEvent.upload(input, pdf());

    expect(await screen.findByText("The EasyApply API is unreachable.")).toBeInTheDocument();
    expect(input.value).toBe("");
    // The chosen name is still shown, so the user knows which file failed.
    expect(screen.getByTestId("chosen-file")).toHaveTextContent("resume.pdf");
  });

  it("reports an oversized file, whose 413 body is nested", async () => {
    server.use(
      http.post("/api/backend/resumes", () =>
        HttpResponse.json(
          { detail: { detail: "The file is larger than the 10 MB limit.", code: "file_too_large" } },
          { status: 413 },
        ),
      ),
    );

    render(<ResumePage />);
    await userEvent.upload(screen.getByLabelText("Resume file"), pdf());

    expect(await screen.findByText("The file is larger than the 10 MB limit.")).toBeInTheDocument();
  });

  it("shows a failed resume as failed in the list", async () => {
    server.use(
      http.get("/api/backend/resumes", () =>
        HttpResponse.json([
          resumeDocument({
            latest_run: run({
              status: "failed",
              failed_stage: "ocr",
              error_code: "ocr_unavailable",
              profile_version_id: null,
            }),
          }),
        ]),
      ),
    );

    render(<ResumePage />);
    expect(await screen.findByText("Failed")).toBeInTheDocument();
  });

  it("does not re-implement the server's file-type rules", async () => {
    /** The accept attribute is a picker hint; the server decides. */
    render(<ResumePage />);
    const input = screen.getByLabelText("Resume file");

    expect(input).toHaveAttribute("accept", expect.stringContaining("application/pdf"));
    expect(input).not.toHaveAttribute("required");
  });
});
