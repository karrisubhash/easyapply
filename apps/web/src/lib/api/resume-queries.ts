"use client";

import { ACTIVE_PROCESSING_STATUSES, type ResumeDocument } from "@easyapply/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import * as api from "./resume";

export const resumeKeys = {
  all: ["resumes"] as const,
  detail: (id: number) => ["resumes", id] as const,
  result: (id: number) => ["resumes", id, "result"] as const,
};

function retryUnlessExpected(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isSessionFailure || error.isNotFound || error.isConflict || error.isValidation)) {
    return false;
  }
  return failureCount < 2;
}

/** True while the server still intends to do more work on this document. */
export function isProcessing(document: ResumeDocument | undefined): boolean {
  const status = document?.latest_run?.status;
  return (
    status !== undefined &&
    (ACTIVE_PROCESSING_STATUSES as readonly string[]).includes(status)
  );
}

export function useResumes() {
  return useQuery({
    queryKey: resumeKeys.all,
    queryFn: api.listResumes,
    retry: retryUnlessExpected,
    // Processing is synchronous today, so nothing should sit in an active
    // state. The poll exists so the screen still converges when it moves to a
    // worker — the UI already treats "in progress" as a real state.
    refetchInterval: (query) =>
      (query.state.data ?? []).some(isProcessing) ? 2000 : false,
  });
}

export function useResume(documentId: number) {
  return useQuery({
    queryKey: resumeKeys.detail(documentId),
    queryFn: () => api.getResume(documentId),
    retry: retryUnlessExpected,
    enabled: Number.isFinite(documentId),
    refetchInterval: (query) => (isProcessing(query.state.data) ? 2000 : false),
  });
}

export function useResumeResult(documentId: number, enabled = true) {
  return useQuery({
    queryKey: resumeKeys.result(documentId),
    queryFn: () => api.getResumeResult(documentId),
    retry: retryUnlessExpected,
    enabled: enabled && Number.isFinite(documentId),
  });
}

export function useUploadResume() {
  const client = useQueryClient();
  return useMutation<ResumeDocument, ApiError, File>({
    mutationFn: api.uploadResume,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: resumeKeys.all });
      // Processing creates a draft profile version, so the profile tree is stale.
      client.invalidateQueries({ queryKey: ["profile"] });
    },
  });
}

export function useReprocessResume(documentId: number) {
  const client = useQueryClient();
  return useMutation<ResumeDocument, ApiError, void>({
    mutationFn: () => api.reprocessResume(documentId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["resumes"] });
      client.invalidateQueries({ queryKey: ["profile"] });
    },
  });
}
