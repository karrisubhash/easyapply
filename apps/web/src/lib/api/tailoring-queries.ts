"use client";

import {
  TAILORING_ACTIVE_STATUSES,
  type TailoringRun,
  type TailoringRunDetail,
} from "@easyapply/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import * as api from "./tailoring";

export const tailoringKeys = {
  all: ["tailored-resumes"] as const,
  detail: (id: number) => ["tailored-resumes", id] as const,
  forJob: (jobId: number) => ["jobs", jobId, "tailored-resumes"] as const,
};

function retryUnlessExpected(failureCount: number, error: unknown): boolean {
  // A 409 means the job is not structured, the profile has no confirmed
  // version, or the document was not approved. None of those change on a
  // retry, and all three are things to tell the user.
  if (error instanceof ApiError && (error.isSessionFailure || error.isNotFound || error.isConflict)) {
    return false;
  }
  return failureCount < 2;
}

/** True while the server still intends to do more work on this run. */
export function isTailoring(run: { status?: string } | undefined): boolean {
  const status = run?.status;
  return (
    status !== undefined &&
    (TAILORING_ACTIVE_STATUSES as readonly string[]).includes(status)
  );
}

/**
 * Whether this run produced something the user may download.
 *
 * Reads the server's own `has_artifact` rather than inferring it from the
 * status: a COMPLETED run whose document failed grounding has no file, and a
 * UI that decided "completed means downloadable" would show a button that
 * 409s. The server owns that rule — see tailoring/service.py::get_artifact.
 */
export function isDownloadable(run: TailoringRun | TailoringRunDetail | undefined): boolean {
  return Boolean(run?.has_artifact);
}

export function useTailoredResumes() {
  return useQuery({
    queryKey: tailoringKeys.all,
    queryFn: api.listTailoredResumes,
    retry: retryUnlessExpected,
    refetchInterval: (query) =>
      (query.state.data ?? []).some(isTailoring) ? 2000 : false,
  });
}

export function useTailoredResumesForJob(jobId: number, enabled = true) {
  return useQuery({
    queryKey: tailoringKeys.forJob(jobId),
    queryFn: () => api.listTailoredResumesForJob(jobId),
    retry: retryUnlessExpected,
    enabled: enabled && Number.isInteger(jobId) && jobId > 0,
    refetchInterval: (query) =>
      (query.state.data ?? []).some(isTailoring) ? 2000 : false,
  });
}

export function useTailoredResume(runId: number | undefined) {
  return useQuery({
    queryKey: tailoringKeys.detail(runId ?? 0),
    queryFn: () => api.getTailoredResume(runId as number),
    retry: retryUnlessExpected,
    enabled: Number.isInteger(runId) && (runId ?? 0) > 0,
    // Generation happens on a worker, so the page has to converge on its own.
    // Polling stops the moment the run is terminal — including when it
    // completed with a failed grounding verdict, which is a finished answer.
    refetchInterval: (query) => (isTailoring(query.state.data) ? 2000 : false),
  });
}

export function useStartTailoring(jobId: number) {
  const client = useQueryClient();
  return useMutation<
    TailoringRunDetail,
    ApiError,
    { profile_version_id?: number; match_result_id?: number } | undefined
  >({
    mutationFn: (payload) => api.startTailoring(jobId, payload ?? {}),
    onSuccess: (run) => {
      // Seed the detail cache from the response so opening the run does not
      // re-fetch what the POST already returned in full.
      client.setQueryData(tailoringKeys.detail(run.id), run);
      client.invalidateQueries({ queryKey: tailoringKeys.forJob(jobId) });
      client.invalidateQueries({ queryKey: tailoringKeys.all });
    },
  });
}

/**
 * Download the artifact and hand it to the browser.
 *
 * A mutation rather than a query because it is an action with a side effect
 * outside React — it puts a file in the user's downloads folder — and caching
 * that would mean a second click quietly doing nothing.
 *
 * The object URL is revoked shortly AFTER the click, not during it. `click()`
 * only schedules the download; revoking in the same task let some browsers
 * (Firefox, Safari) look the URL up after it was gone and fail the download,
 * or save nothing. Holding it for good would pin a whole document in memory
 * for the life of the tab, so it is released once any browser has long since
 * started reading it.
 */
const REVOKE_AFTER_MS = 10_000;

export function useDownloadArtifact() {
  return useMutation<void, ApiError, number>({
    mutationFn: async (runId: number) => {
      const { blob, filename } = await api.downloadArtifact(runId);

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
    },
  });
}
