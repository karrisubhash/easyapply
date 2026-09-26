/**
 * The Resume API, one function per endpoint.
 *
 * Upload is the one call that does not go through `request()`: it sends
 * multipart form data rather than JSON, so it sets no Content-Type and lets the
 * browser write the boundary.
 */

import type {
  ProcessingResult,
  ResumeDocument,
  ResumeDocumentDetail,
} from "@easyapply/shared";

import { ApiError, apiFetch, parseBody, request } from "./client";

export function listResumes(): Promise<ResumeDocument[]> {
  return request<ResumeDocument[]>("resumes");
}

export function getResume(documentId: number): Promise<ResumeDocumentDetail> {
  return request<ResumeDocumentDetail>(`resumes/${documentId}`);
}

export function getResumeResult(documentId: number): Promise<ProcessingResult> {
  return request<ProcessingResult>(`resumes/${documentId}/result`);
}

export function reprocessResume(documentId: number): Promise<ResumeDocument> {
  return request<ResumeDocument>(`resumes/${documentId}/reprocess`, { method: "POST" });
}

export async function uploadResume(file: File): Promise<ResumeDocument> {
  const body = new FormData();
  body.append("file", file);

  const response = await apiFetch("resumes", { method: "POST", body });
  // An upload is the request most likely to be refused by something that is not
  // the API — a proxy, a body-size limit — and those answer in HTML.
  const payload: unknown = parseBody(await response.text());

  if (!response.ok) {
    const problem = payload as { detail?: unknown; code?: string };
    // A 413 from the edge nests its body under `detail`.
    const nested =
      problem?.detail && typeof problem.detail === "object"
        ? (problem.detail as { detail?: string; code?: string })
        : null;

    throw new ApiError(
      response.status,
      nested?.code ?? problem?.code ?? "unknown",
      nested?.detail ??
        (typeof problem?.detail === "string" ? problem.detail : "The upload failed."),
    );
  }

  return payload as ResumeDocument;
}
