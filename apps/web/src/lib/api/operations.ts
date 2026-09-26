/**
 * The internal operations API.
 *
 * Separate from the resume API because it is a different surface with a
 * different audience and a different authorization boundary — the proxy
 * attaches an operator token for `internal/` paths and nothing else. Mixing
 * these calls into resume.ts would blur that line.
 */

import type {
  OperatorAttention,
  OperatorDocumentDetail,
  OperatorRunDetail,
} from "@easyapply/shared";

import { request } from "./client";

const BASE = "internal/resume-processing";

export function getAttention(includeAll = false): Promise<OperatorAttention> {
  return request<OperatorAttention>(
    `${BASE}/attention${includeAll ? "?include_all=true" : ""}`,
  );
}

export function getOperatorDocument(
  documentId: number,
): Promise<OperatorDocumentDetail> {
  return request<OperatorDocumentDetail>(`${BASE}/documents/${documentId}`);
}

export function getOperatorRun(runId: number): Promise<OperatorRunDetail> {
  return request<OperatorRunDetail>(`${BASE}/runs/${runId}`);
}
