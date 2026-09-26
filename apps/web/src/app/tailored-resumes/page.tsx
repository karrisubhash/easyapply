"use client";

import type { TailoringRun } from "@easyapply/shared";
import Link from "next/link";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard } from "@/components/profile/states";
import { TailoringStatusBadge } from "@/components/tailoring/tailoring-status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useTailoredResumes } from "@/lib/api/tailoring-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * Every resume generated, newest first.
 *
 * The row says which job and which profile version, because those two facts
 * together are what make one of these documents different from another — a
 * listing keyed only on the job would show three indistinguishable rows to
 * someone who regenerated after confirming a new version.
 */
function reviewName(run: TailoringRun): string {
  return [
    `Review ${run.job_title ?? "untitled job"}${run.company_name ? ` at ${run.company_name}` : ""}`,
    run.profile_version_number ? `from version ${run.profile_version_number}` : null,
    run.attempt > 1 ? `attempt ${run.attempt}` : null,
  ]
    .filter(Boolean)
    .join(", ");
}

export default function TailoredResumesPage() {
  const runs = useTailoredResumes();

  usePageTitle("Tailored resumes");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Tailored resumes</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Resumes generated from a confirmed profile version for one job. Every statement
          is checked back against the version it came from before the file is offered.
        </p>
      </div>

      {runs.isPending ? <LoadingCard label="Loading tailored resumes" /> : null}
      {runs.isError ? <ApiErrorAlert error={runs.error} onRetry={() => void runs.refetch()} /> : null}

      {runs.isSuccess && runs.data.length === 0 ? (
        <EmptyState
          title="Nothing generated yet"
          description="Open a job and choose Tailor resume. You will pick which confirmed profile version to build from, and review every line before downloading."
          action={
            <Button asChild>
              <Link href="/jobs">Go to jobs</Link>
            </Button>
          }
        />
      ) : null}

      {runs.isSuccess && runs.data.length > 0 ? (
        <ul className="space-y-3">
          {runs.data.map((run) => (
            <li key={run.id}>
              <Card>
                <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-2 font-medium">
                      <span className="truncate">
                        {run.job_title ?? "Untitled job"}
                      </span>
                      <TailoringStatusBadge run={run} />
                    </p>
                    <p className="mt-1 truncate text-xs text-muted-foreground">
                      {[
                        run.company_name,
                        run.profile_version_number
                          ? `From profile version ${run.profile_version_number}`
                          : null,
                        run.attempt > 1 ? `Attempt ${run.attempt}` : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Generated {formatDateTime(run.created_at)}
                    </p>
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link
                      href={`/tailored-resumes/${run.id}`}
                      // Named for the row it sits in. A column of identical
                      // "Review"s tells a screen-reader user nothing about
                      // which document each one opens. Attempts are counted
                      // per job AND version, so "attempt 2" alone named two
                      // different documents the same; the company separates
                      // one title at two companies. The visible row carries
                      // the same facts.
                      aria-label={reviewName(run)}
                    >
                      Review
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
