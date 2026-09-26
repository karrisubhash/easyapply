import { TAILORING_ACTIVE_STATUSES } from "@easyapply/shared";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  tailoredResume,
  tailoringIssue,
  tailoringRun,
  tailoringRunDetail,
  tailoringRunInFlight,
} from "@/test/tailoring-fixtures";
import { apiError, server } from "@/test/server";

import { ApiError } from "./client";
import { downloadArtifact, filenameFrom } from "./tailoring";
import { isDownloadable, isTailoring } from "./tailoring-queries";

/**
 * The tailoring reading helpers and the one call that is not JSON.
 *
 * Two questions the rest of the UI asks constantly: is this run still moving,
 * and may the user have the file. Getting the first wrong leaves a spinner up
 * forever or stops polling on a run that is still working; getting the second
 * wrong hands out a resume the grounding check refused to stand behind.
 */

/** Every status the pipeline can be in — see app/models/tailoring.py. */
const ALL_STATUSES = [
  "queued",
  "generating",
  "validating",
  "rendering",
  "completed",
  "failed",
] as const;

/** Narrows to ApiError so the assertions below are typed, not `unknown`. */
async function catchApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
    throw new Error("expected the download to reject");
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return error;
  }
}

describe("isTailoring", () => {
  it("treats exactly the four pre-terminal statuses as work in progress", () => {
    // A status added server-side and forgotten here would either freeze the
    // page on a run that is still moving or poll a finished one forever.
    expect(ALL_STATUSES.filter((status) => isTailoring({ status }))).toEqual([
      "queued",
      "generating",
      "validating",
      "rendering",
    ]);
  });

  it("knows about every status the shared list calls active", () => {
    // Guards the enumeration above: if shared gains an active status, the
    // partition test must be told about it rather than quietly skipping it.
    expect(ALL_STATUSES).toEqual(expect.arrayContaining([...TAILORING_ACTIVE_STATUSES]));
  });

  it("stops at completed, including when grounding failed", () => {
    // A completed run with a failed verdict is a finished answer, not a stall.
    // Polling it again would never change anything and the page would never
    // settle.
    expect(isTailoring(tailoringRun({ status: "completed" }))).toBe(false);
    expect(
      isTailoring(tailoringRunDetail({ result: tailoredResume({ issues: [tailoringIssue()] }) })),
    ).toBe(false);
  });

  it("stops at failed", () => {
    expect(isTailoring(tailoringRun({ status: "failed" }))).toBe(false);
  });

  it("says no for a run that is not there yet", () => {
    // The detail query starts undefined, and a helper that threw or answered
    // true here would poll a run that does not exist.
    expect(isTailoring(undefined)).toBe(false);
    expect(isTailoring({})).toBe(false);
  });

  it("says no for a status it does not recognise", () => {
    // An unknown status means this client is older than the server. Treating
    // it as finished shows a stopped run; treating it as active would spin
    // against an endpoint whose answer never changes.
    expect(isTailoring({ status: "cancelled" })).toBe(false);
    expect(isTailoring({ status: "" })).toBe(false);
  });

  it("recognises a run in flight the way the fixture builds it", () => {
    expect(isTailoring(tailoringRunInFlight("generating"))).toBe(true);
    expect(isTailoring(tailoringRunInFlight("rendering"))).toBe(true);
  });
});

describe("isDownloadable", () => {
  it("offers the file when the server says an artifact exists", () => {
    expect(isDownloadable(tailoringRun({ has_artifact: true }))).toBe(true);
  });

  it("refuses a COMPLETED run that has no artifact", () => {
    // The whole reason this helper exists. A document that failed grounding
    // completes normally and has no file: a UI that read `status` instead
    // would show a download button that 409s, over a resume we will not vouch
    // for.
    const failedGrounding = tailoringRunDetail({
      result: tailoredResume({ issues: [tailoringIssue()] }),
    });

    expect(failedGrounding.status).toBe("completed");
    expect(failedGrounding.grounding_status).toBe("failed");
    expect(isDownloadable(failedGrounding)).toBe(false);
  });

  it("does not infer downloadability from a completed status alone", () => {
    expect(isDownloadable(tailoringRun({ status: "completed", has_artifact: false }))).toBe(false);
  });

  it("refuses a run that is still generating", () => {
    expect(isDownloadable(tailoringRunInFlight("rendering"))).toBe(false);
  });

  it("refuses a failed run", () => {
    expect(
      isDownloadable(tailoringRun({ status: "failed", has_artifact: false })),
    ).toBe(false);
  });

  it("refuses rather than throwing before the run has loaded", () => {
    expect(isDownloadable(undefined)).toBe(false);
  });
});

describe("filenameFrom", () => {
  it("reads the name the server chose", () => {
    // The server builds it from the company, the title and the version. The
    // client displays and saves that name, it does not compose one.
    expect(
      filenameFrom('attachment; filename="Acme-Corp-Senior-Backend-Engineer-v1.docx"'),
    ).toBe("Acme-Corp-Senior-Backend-Engineer-v1.docx");
  });

  it("accepts an unquoted filename", () => {
    expect(filenameFrom("attachment; filename=tailored-resume-7.docx")).toBe(
      "tailored-resume-7.docx",
    );
  });

  it("stops at a trailing parameter rather than swallowing it", () => {
    // Saving the file as `resume.docx; charset=utf-8` would be a broken name
    // on every operating system.
    expect(filenameFrom("attachment; filename=resume.docx; charset=utf-8")).toBe("resume.docx");
    expect(
      filenameFrom("attachment; filename=\"resume.docx\"; filename*=UTF-8''resume.docx"),
    ).toBe("resume.docx");
  });

  it("matches the header parameter case-insensitively", () => {
    // Header parameter names are not case sensitive, and a proxy may rewrite
    // them. Missing that would silently downgrade every download to the
    // fallback name.
    expect(filenameFrom('attachment; FileName="Upper-Case.docx"')).toBe("Upper-Case.docx");
  });

  it("falls back to a dull name instead of refusing the download", () => {
    // A missing or empty header is a reason to save under a boring name, never
    // a reason to withhold a resume the user is entitled to.
    expect(filenameFrom(null)).toBe("tailored-resume.docx");
    expect(filenameFrom("attachment")).toBe("tailored-resume.docx");
    expect(filenameFrom('attachment; filename=""')).toBe("tailored-resume.docx");
    expect(filenameFrom("attachment; filename=   ")).toBe("tailored-resume.docx");
  });

  it("never lets a quote out of the header into the filename", () => {
    // This string becomes an anchor's download attribute. A stray quote from a
    // malformed header must not end up in the saved name.
    const name = filenameFrom('attachment; filename="broken".docx"');

    expect(name).not.toContain('"');
    expect(name.length).toBeGreaterThan(0);
  });

  it("always answers with a non-empty name", () => {
    // Whatever arrives, the user gets a file with a name — an empty download
    // attribute leaves the browser inventing one from the URL.
    for (const header of ["", "attachment;", "attachment; filename=;", "inline"]) {
      expect(filenameFrom(header)).toBe("tailored-resume.docx");
    }
  });
});

describe("downloadArtifact", () => {
  it("returns the bytes and the name the server gave them", async () => {
    let requestedId: string | undefined;
    server.use(
      http.get("/api/backend/tailored-resumes/:id/artifact", ({ params }) => {
        requestedId = String(params.id);
        return HttpResponse.arrayBuffer(new TextEncoder().encode("PK stub").buffer, {
          headers: {
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "Content-Disposition": 'attachment; filename="Acme-Corp-v1.docx"',
          },
        });
      }),
    );

    const { blob, filename } = await downloadArtifact(12);

    // The run in the path is the run that was asked for: a download must not
    // hand back somebody else's resume.
    expect(requestedId).toBe("12");
    expect(await blob.text()).toBe("PK stub");
    expect(filename).toBe("Acme-Corp-v1.docx");
  });

  it("raises the server's own refusal as an ApiError", async () => {
    // The refusal the grounding gate produces. It has to arrive as the same
    // ApiError every other call throws, carrying the API's sentence, because
    // that sentence is what the user reads.
    server.use(
      http.get("/api/backend/tailored-resumes/:id/artifact", () =>
        apiError(
          409,
          "This resume contains statements that could not be traced back to your profile, so it was not approved for download.",
          "tailoring_not_approved",
        ),
      ),
    );

    const error = await catchApiError(downloadArtifact(1));

    expect(error.status).toBe(409);
    expect(error.isConflict).toBe(true);
    expect(error.code).toBe("tailoring_not_approved");
    expect(error.message).toBe(
      "This resume contains statements that could not be traced back to your profile, so it was not approved for download.",
    );
  });

  it("keeps the code for an artifact that is gone", async () => {
    server.use(
      http.get("/api/backend/tailored-resumes/:id/artifact", () =>
        apiError(404, "This resume has no file to download.", "not_found"),
      ),
    );

    const error = await catchApiError(downloadArtifact(1));

    expect(error.isNotFound).toBe(true);
    expect(error.code).toBe("not_found");
  });

  it("reports a non-JSON failure as an ApiError, not a parser crash", async () => {
    // A crash in the proxy answers with HTML. Parsing a zip endpoint's error
    // body optimistically is how "Unexpected token <" reaches the user
    // instead of a sentence.
    server.use(
      http.get("/api/backend/tailored-resumes/:id/artifact", () =>
        HttpResponse.text("<!doctype html><title>502 Bad Gateway</title>", { status: 502 }),
      ),
    );

    const error = await catchApiError(downloadArtifact(1));

    expect(error.status).toBe(502);
    expect(error.code).toBe("unknown");
    expect(error.message).toBe("The resume could not be downloaded.");
    expect(error.message).not.toMatch(/doctype|Gateway|</);
  });

  it("does not hand back a blob of the error body", async () => {
    // Without the ok check the caller would save an HTML page or a JSON error
    // under a .docx name — a download that appears to succeed and produces a
    // corrupt file.
    server.use(
      http.get("/api/backend/tailored-resumes/:id/artifact", () =>
        apiError(409, "This resume has not finished generating yet.", "conflict"),
      ),
    );

    await expect(downloadArtifact(1)).rejects.toBeInstanceOf(ApiError);
  });
});

/**
 * Regression: the filename comes from a header and becomes a file on disk.
 *
 * The server sanitises the name it sends (`rendering.build_filename`), so a
 * separator should never arrive. This is the layer that must not depend on that
 * having been true — it is the last step before something is written to the
 * user's machine.
 */
describe("filenameFrom — a header is not a trusted source", () => {
  it.each([
    ['attachment; filename="../../etc/passwd"', "passwd"],
    ['attachment; filename="..\\windows\\evil.docx"', "evil.docx"],
    ['attachment; filename="/absolute/resume.docx"', "resume.docx"],
    ['attachment; filename="a/b/c/resume.docx"', "resume.docx"],
  ])("keeps only the last segment of %s", (header, expected) => {
    expect(filenameFrom(header)).toBe(expected);
  });

  it("falls back when the header leaves nothing but dots behind", () => {
    // `../` sanitises to `..`, which is a directory reference and not a name.
    expect(filenameFrom('attachment; filename="../"')).toBe("tailored-resume.docx");
    expect(filenameFrom('attachment; filename=".."')).toBe("tailored-resume.docx");
  });

  it("leaves an ordinary filename exactly as the server wrote it", () => {
    expect(filenameFrom('attachment; filename="Acme-Corp-Engineer-v3.docx"')).toBe(
      "Acme-Corp-Engineer-v3.docx",
    );
  });
});
