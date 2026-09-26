"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { JobRequirementsReview } from "@/components/job/job-requirements-review";
import {
  JobStatusBadge,
  jobFailureExplanation,
  jobFailureTitle,
  jobStatusLabel,
} from "@/components/job/job-status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { isJobProcessing, useJob, useJobRequirements, useReprocessJob } from "@/lib/api/job-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime, humanise } from "@/lib/utils";

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = Number(params.id);

  // Hooks run unconditionally, before any early return — useJob leaves its
  // query disabled for a non-numeric id, so calling it is always safe even
  // though the id itself might address nothing.
  const job = useJob(jobId);
  const latestStatus = job.data?.latest_run?.status;
  const isDone = latestStatus === "completed";
  // A failed REPROCESS leaves the previous reading in force: matching and
  // tailoring keep using the newest completed requirements, and the API returns
  // them. Asking only after a completed latest run hid a reading every other
  // part of the product was still acting on.
  const requirements = useJobRequirements(jobId, isDone || latestStatus === "failed");
  const hasReading = Boolean(requirements.data?.requirements);
  const readingIsFromEarlierAttempt =
    hasReading &&
    latestStatus === "failed" &&
    requirements.data?.run?.id !== job.data?.latest_run?.id;
  const reprocess = useReprocessJob(jobId);

  usePageTitle(job.data?.job_title ?? "Job", job.data?.company_name);

  const notFound = (
    <EmptyState
      title="That job does not exist"
      description="It may have been removed, or the link may be wrong."
      action={
        <Button variant="outline" asChild>
          <Link href="/jobs">Back to jobs</Link>
        </Button>
      }
    />
  );

  // A segment that is not a job id addresses nothing, and useJob leaves the
  // query disabled — which would otherwise render as a permanent skeleton.
  if (!Number.isInteger(jobId) || jobId < 1) return <PageState heading="Job">{notFound}</PageState>;

  if (job.isPending) {
    return (
      <PageState heading="Job">
        <LoadingCard label="Loading job" />
      </PageState>
    );
  }

  if (job.isError && job.error instanceof ApiError && job.error.isNotFound) {
    return <PageState heading="Job">{notFound}</PageState>;
  }

  if (job.isError) {
    return (
      <PageState heading="Job">
        <ApiErrorAlert error={job.error} onRetry={() => void job.refetch()} />
      </PageState>
    );
  }

  const run = job.data.latest_run;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <span className="truncate">{job.data.job_title}</span>
            <JobStatusBadge run={run} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {job.data.company_name ?? "Unknown company"}
            {job.data.location_raw ? ` · ${job.data.location_raw}` : ""}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            <Badge variant="outline">{job.data.source_type}</Badge>
            {job.data.employment_type ? (
              <Badge variant="outline">{humanise(job.data.employment_type)}</Badge>
            ) : null}
            {job.data.workplace_type ? (
              <Badge variant="outline">{humanise(job.data.workplace_type)}</Badge>
            ) : null}
            <span>Saved {formatDateTime(job.data.created_at)}</span>
          </p>
        </div>
        <Button variant="outline" asChild>
          <a href={job.data.source_url} target="_blank" rel="noreferrer noopener">
            View original
          </a>
        </Button>
      </div>

      {(job.data.salary_min || job.data.salary_max) ? (
        <p className="text-sm text-muted-foreground">
          {formatSalary(job.data)}
        </p>
      ) : null}

      {isJobProcessing(job.data) ? (
        <Card>
          <CardContent className="p-6">
            <p role="status" className="text-sm">
              {jobStatusLabel(run!.status)}…
            </p>
          </CardContent>
        </Card>
      ) : null}

      {run?.status === "failed" ? (
        <>
          <Alert variant="destructive">
            <AlertTitle>{jobFailureTitle(run)}</AlertTitle>
            <AlertDescription>
              <p>{jobFailureExplanation(run)}</p>
              {run.error_code ? (
                <p className="mt-2 text-xs opacity-70">Reference: {run.error_code}</p>
              ) : null}
            </AlertDescription>
          </Alert>
          {/* Whether a retry is worth offering is the server's judgement, not a
              rule re-derived here from the error code — two copies of that
              rule would be two answers free to disagree. */}
          {run.can_retry ? (
            <div className="flex items-center gap-2">
              <Button onClick={() => reprocess.mutate()} disabled={reprocess.isPending}>
                {reprocess.isPending ? "Retrying…" : "Try again"}
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Another attempt would fail the same way without a configuration or code change.
            </p>
          )}
        </>
      ) : null}

      {readingIsFromEarlierAttempt ? (
        <Alert>
          <AlertTitle>Showing the last reading that succeeded</AlertTitle>
          <AlertDescription>
            The latest attempt to read this posting failed, so the requirements below are from
            the attempt before it. Matching and tailoring use this same reading.
          </AlertDescription>
        </Alert>
      ) : null}

      {isDone || readingIsFromEarlierAttempt ? (
        <div className="flex flex-wrap items-center gap-2">
          {/* Both actions live here, on the job, because that is where the user
              is when they want either — §6 asks that neither takes a walk
              through unrelated pages to reach. Offered only once the job has
              been structured: both need a requirements row to pin, and showing
              them earlier would send the user to a page that can only tell them
              to come back. */}
          <Button size="sm" asChild>
            <Link href={`/jobs/${jobId}/match`}>Match my profile</Link>
          </Button>
          <Button size="sm" variant="secondary" asChild>
            <Link href={`/jobs/${jobId}/tailor`}>Tailor resume</Link>
          </Button>
          {/* After a failed attempt, the failure alert above already offers the
              retry; a second button doing the same thing is noise. */}
          {isDone ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => reprocess.mutate()}
              disabled={reprocess.isPending}
            >
              {reprocess.isPending ? "Reprocessing…" : "Reprocess"}
            </Button>
          ) : null}
        </div>
      ) : null}

      {/* Outside both branches above. It used to live inside the failed-run
          fragment only, so a refused Reprocess on a COMPLETED job — the other
          place the button is offered — failed in silence. */}
      {reprocess.isError ? <ApiErrorAlert error={reprocess.error} /> : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Description</CardTitle>
        </CardHeader>
        <CardContent>
          {job.data.description_text ? (
            <pre className="whitespace-pre-wrap break-words font-sans text-sm text-foreground">
              {job.data.description_text}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              No description was captured for this posting.
            </p>
          )}
        </CardContent>
      </Card>

      {requirements.isPending && (isDone || latestStatus === "failed") ? (
        <LoadingCard label="Loading requirements" />
      ) : null}
      {requirements.isError ? (
        <ApiErrorAlert error={requirements.error} onRetry={() => void requirements.refetch()} />
      ) : null}

      {requirements.data?.requirements ? (
        <>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Interpreted by {requirements.data.requirements.structurer}</span>
            {requirements.data.requirements.model ? (
              <span>({requirements.data.requirements.model})</span>
            ) : null}
          </div>
          <JobRequirementsReview candidate={requirements.data.requirements.candidate} />
        </>
      ) : null}
    </div>
  );
}

function formatSalary(job: { salary_min: number | null; salary_max: number | null; salary_currency: string | null; salary_period: string | null }): string {
  const currency = job.salary_currency ?? "";
  const period = job.salary_period ? `/${job.salary_period}` : "";
  if (job.salary_min != null && job.salary_max != null) {
    return `${currency} ${job.salary_min.toLocaleString()} – ${job.salary_max.toLocaleString()}${period}`;
  }
  const single = job.salary_min ?? job.salary_max;
  return `${currency} ${single?.toLocaleString()}${period}`;
}
