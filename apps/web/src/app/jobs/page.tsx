"use client";

/**
 * Job listing.
 *
 * Jobs can be added here as well as by the (future) Chrome extension, and both
 * go through the SAME ingestion endpoint — see `app/jobs/new/page.tsx`. The
 * earlier concern that a web form would be a second hand-typed ingestion path
 * is answered by not building one: the form posts `JobIngestionRequest`, and
 * every validator, normalizer and deduplication rule the extension will meet is
 * the one it meets too.
 */

import Link from "next/link";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard } from "@/components/profile/states";
import { JobStatusBadge } from "@/components/job/job-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useJobs } from "@/lib/api/job-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function JobsPage() {
  const jobs = useJobs();

  usePageTitle("Jobs");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Jobs</h1>
          <p className="text-sm text-muted-foreground">
            Postings saved and read into structured requirements you can match and tailor
            against.
          </p>
        </div>
        <Button asChild>
          <Link href="/jobs/new">Add a job</Link>
        </Button>
      </div>

      {jobs.isPending ? <LoadingCard label="Loading jobs" /> : null}
      {jobs.isError ? <ApiErrorAlert error={jobs.error} onRetry={() => void jobs.refetch()} /> : null}

      {jobs.isSuccess && jobs.data.length === 0 ? (
        <EmptyState
          title="No jobs yet"
          description="Add a posting to see its requirements, match your profile against it, and generate a resume for it."
          action={
            <Button asChild>
              <Link href="/jobs/new">Add a job</Link>
            </Button>
          }
        />
      ) : null}

      {jobs.isSuccess && jobs.data.length > 0 ? (
        <ul className="space-y-3">
          {jobs.data.map((job) => (
            <li key={job.id}>
              <Card>
                <CardContent className="flex items-center justify-between gap-4 p-4">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <span className="truncate">{job.job_title}</span>
                      <JobStatusBadge run={job.latest_run} />
                    </p>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {job.company_name ?? "Unknown company"}
                      {job.location_raw ? ` · ${job.location_raw}` : ""}
                      {" · "}
                      {job.source_type}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Saved {formatDateTime(job.created_at)}
                    </p>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    {/* The row's own name, for anyone reading the page as a
                        list of links: a column of identical "View"s says
                        nothing about which posting each one opens. The visible
                        label stays short. The company is part of it because
                        one title at two companies is two different links. */}
                    <Link
                      href={`/jobs/${job.id}`}
                      aria-label={`View ${job.job_title}${
                        job.company_name ? ` at ${job.company_name}` : ""
                      }`}
                    >
                      View
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
