/**
 * The Tailoring API, one function per endpoint.
 *
 * `startTailoring` is a POST that is safe to repeat: the server answers 201 for
 * a new run and 200 with the one already in flight for the same job and
 * version, so a double-click cannot start two generations. Unlike matching, a
 * repeat AFTER one finished is a genuinely new run — a tailored resume is a
 * draft the user may reasonably want regenerated, where a match is a judgment
 * that should give the same answer twice.
 *
 * The artifact is the one thing here that is not JSON. It is fetched as a blob
 * through the same proxy, which passes the API's own Content-Type and
 * Content-Disposition through — the filename is decided server-side from the
 * artifact row, and this file does not invent one.
 */

import type {
  TailoringRequest,
  TailoringRun,
  TailoringRunDetail,
} from "@easyapply/shared";

import { ApiError, apiFetch, parseBody, request } from "./client";

export function listTailoredResumes(): Promise<TailoringRun[]> {
  return request<TailoringRun[]>("tailored-resumes");
}

export function listTailoredResumesForJob(jobId: number): Promise<TailoringRun[]> {
  return request<TailoringRun[]>(`jobs/${jobId}/tailored-resumes`);
}

export function getTailoredResume(runId: number): Promise<TailoringRunDetail> {
  return request<TailoringRunDetail>(`tailored-resumes/${runId}`);
}

export function startTailoring(
  jobId: number,
  payload: TailoringRequest = {},
): Promise<TailoringRunDetail> {
  return request<TailoringRunDetail>(`jobs/${jobId}/tailored-resumes`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** A downloaded artifact: the bytes, and what the server called them. */
export interface ArtifactDownload {
  blob: Blob;
  filename: string;
}

/**
 * Fetch the generated file.
 *
 * Not routed through `request()`, which parses JSON — a DOCX is a zip and
 * JSON.parse over it produces nothing useful. The error path still is: a
 * refusal (not finished, not approved, not yours) comes back as the API's
 * ordinary error body, and it is turned into the same ApiError every other call
 * produces so the UI has one thing to render.
 */
export async function downloadArtifact(runId: number): Promise<ArtifactDownload> {
  const response = await apiFetch(`tailored-resumes/${runId}/artifact`);

  if (!response.ok) {
    const problem = parseBody(await response.text()) as
      | { detail?: string; code?: string }
      | null;
    throw new ApiError(
      response.status,
      problem?.code ?? "unknown",
      problem?.detail ?? "The resume could not be downloaded.",
    );
  }

  return {
    blob: await response.blob(),
    filename: filenameFrom(response.headers.get("content-disposition")),
  };
}

/**
 * The filename the server chose, read out of Content-Disposition.
 *
 * Falls back rather than throwing: a missing header is a reason to save the
 * file under a dull name, not a reason to refuse the user their resume.
 *
 * Only the last path segment is kept. The server builds this name from a job
 * title and a company name that came from a third-party posting and sanitises
 * it there (`rendering.build_filename`), so a separator should never arrive —
 * but this value goes straight into an anchor's `download` attribute, and the
 * one place that should not depend on an upstream having got it right is the
 * place that writes a file to the user's disk. Browsers sanitise `download`
 * themselves, which makes this defence in depth rather than a live hole.
 */
export function filenameFrom(header: string | null): string {
  const match = header?.match(/filename="?([^";]+)"?/i);
  const segment = match?.[1]?.trim().split(/[\\/]/).pop()?.trim();
  // A header of `filename="../"` leaves nothing but dots behind.
  if (!segment || /^\.+$/.test(segment)) return "tailored-resume.docx";
  return segment;
}
