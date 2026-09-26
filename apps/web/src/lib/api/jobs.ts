/**
 * The Job API, one function per endpoint.
 *
 * `createJob` posts the same `JobIngestionRequest` the Chrome extension will:
 * one endpoint, one validation path, one normalizer, one deduplication rule.
 * The web form is a second CALLER of ingestion, never a second ingestion path —
 * a hand-written "save a job" endpoint alongside it would be a way for the two
 * to disagree about what a job is.
 */

import type {
  Job,
  JobDetail,
  JobIngestionRequest,
  JobRequirementsResult,
} from "@easyapply/shared";

import { request, requestWithStatus } from "./client";

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("jobs");
}

export function getJob(jobId: number): Promise<JobDetail> {
  return request<JobDetail>(`jobs/${jobId}`);
}

export function getJobRequirements(jobId: number): Promise<JobRequirementsResult> {
  return request<JobRequirementsResult>(`jobs/${jobId}/requirements`);
}

/**
 * What ingestion did: `created` is false when this posting (same source, same
 * link) was already saved, in which case `job` is that existing row UNCHANGED —
 * nothing in this request was stored. The server says so only through the
 * status code, 201 against 200.
 */
export interface JobIngestion {
  job: JobDetail;
  created: boolean;
}

export async function createJob(payload: JobIngestionRequest): Promise<JobIngestion> {
  const { data, status } = await requestWithStatus<JobDetail>("jobs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { job: data, created: status === 201 };
}

export function reprocessJob(jobId: number): Promise<JobDetail> {
  return request<JobDetail>(`jobs/${jobId}/reprocess`, { method: "POST" });
}
