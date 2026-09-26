"use client";

import {
  JOB_ACTIVE_PROCESSING_STATUSES,
  type Job,
  type JobIngestionRequest,
} from "@easyapply/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import * as api from "./jobs";

export const jobKeys = {
  all: ["jobs"] as const,
  detail: (id: number) => ["jobs", id] as const,
  requirements: (id: number) => ["jobs", id, "requirements"] as const,
};

function retryUnlessExpected(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isSessionFailure || error.isNotFound || error.isConflict)) {
    return false;
  }
  return failureCount < 2;
}

/**
 * True while the server still intends to do more work on this job.
 *
 * Processing is synchronous in this milestone — every response already
 * carries a terminal run — so this is never actually true today. It exists
 * anyway, the same reasoning `resume-queries.ts` gives for its own version:
 * when a real AI structurer makes this slow enough to move off the request
 * thread, the UI already treats "in progress" as a real state rather than
 * needing that concept retrofitted later.
 */
export function isJobProcessing(job: Job | undefined): boolean {
  const status = job?.latest_run?.status;
  return (
    status !== undefined &&
    (JOB_ACTIVE_PROCESSING_STATUSES as readonly string[]).includes(status)
  );
}

export function useJobs() {
  return useQuery({
    queryKey: jobKeys.all,
    queryFn: api.listJobs,
    retry: retryUnlessExpected,
    refetchInterval: (query) =>
      (query.state.data ?? []).some(isJobProcessing) ? 2000 : false,
  });
}

export function useJob(jobId: number) {
  return useQuery({
    queryKey: jobKeys.detail(jobId),
    queryFn: () => api.getJob(jobId),
    retry: retryUnlessExpected,
    enabled: Number.isInteger(jobId) && jobId > 0,
    refetchInterval: (query) => (isJobProcessing(query.state.data) ? 2000 : false),
  });
}

export function useJobRequirements(jobId: number, enabled = true) {
  return useQuery({
    queryKey: jobKeys.requirements(jobId),
    queryFn: () => api.getJobRequirements(jobId),
    retry: retryUnlessExpected,
    enabled: enabled && Number.isInteger(jobId) && jobId > 0,
  });
}

export function useCreateJob() {
  const client = useQueryClient();
  return useMutation<api.JobIngestion, ApiError, JobIngestionRequest>({
    mutationFn: api.createJob,
    onSuccess: ({ job }) => {
      // Ingestion structures the job in the same call, so the response is the
      // finished article — seeding the detail cache means opening it does not
      // re-fetch what we already hold.
      client.setQueryData(jobKeys.detail(job.id), job);
      client.invalidateQueries({ queryKey: jobKeys.all });
    },
  });
}

export function useReprocessJob(jobId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.reprocessJob(jobId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: jobKeys.detail(jobId) });
      client.invalidateQueries({ queryKey: jobKeys.requirements(jobId) });
      client.invalidateQueries({ queryKey: jobKeys.all });
    },
  });
}
