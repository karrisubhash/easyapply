"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import * as api from "./operations";

export const operationsKeys = {
  attention: (includeAll: boolean) => ["operations", "attention", includeAll] as const,
  document: (id: number) => ["operations", "document", id] as const,
};

/** A 403 means the token is wrong, and retrying with it will not help. */
function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isSessionFailure || error.status === 403 || error.isNotFound)) {
    return false;
  }
  return failureCount < 2;
}

export function useAttention(includeAll: boolean) {
  return useQuery({
    queryKey: operationsKeys.attention(includeAll),
    queryFn: () => api.getAttention(includeAll),
    retry: retryUnlessRefused,
    // An operations view is only useful if it is current. Cheap: the response
    // is ids and counts, and nobody leaves this page open by accident.
    refetchInterval: 10_000,
    staleTime: 0,
  });
}

export function useOperatorDocument(documentId: number | null) {
  return useQuery({
    queryKey: operationsKeys.document(documentId ?? -1),
    queryFn: () => api.getOperatorDocument(documentId as number),
    retry: retryUnlessRefused,
    enabled: documentId !== null,
  });
}
