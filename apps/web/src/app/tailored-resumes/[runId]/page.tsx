"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { GroundingSummary } from "@/components/tailoring/grounding-summary";
import { TailoredResumeView } from "@/components/tailoring/tailored-resume-view";
import {
  TailoringStatusBadge,
  tailoringFailureExplanation,
  tailoringFailureTitle,
  tailoringStatusLabel,
} from "@/components/tailoring/tailoring-status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import {
  isDownloadable,
  isTailoring,
  useDownloadArtifact,
  useTailoredResume,
} from "@/lib/api/tailoring-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * Review a generated resume, then download it.
 *
 * The order on this page is the argument it is making. Provenance first — which
 * job, which profile version, which generator — then the grounding verdict,
 * then the document, then the download. A user who scrolls to the button has
 * already passed the sentence saying whether the document can be stood behind,
 * and a user who scrolls past that has seen which snapshot it came from.
 *
 * The download button is absent, not disabled, when there is no file. A
 * disabled button says "you cannot do this yet"; there is no yet here — a
 * document that failed grounding will never have a file, and the way forward is
 * to generate again or fix the profile, both of which are offered instead.
 */
export default function TailoredResumePage() {
  const params = useParams<{ runId: string }>();
  const runId = Number(params.runId);

  const run = useTailoredResume(runId);
  const download = useDownloadArtifact();

  usePageTitle("Tailored resume", run.data?.job_title ?? (run.data ? "Untitled job" : null));

  const notFound = (
    <EmptyState
      title="That tailored resume does not exist"
      description="It may have been removed, or the link may be wrong."
      action={
        <Button variant="outline" asChild>
          <Link href="/tailored-resumes">All tailored resumes</Link>
        </Button>
      }
    />
  );

  if (!Number.isInteger(runId) || runId < 1) {
    return <PageState heading="Tailored resume">{notFound}</PageState>;
  }
  if (run.isPending) {
    return (
      <PageState heading="Tailored resume">
        <LoadingCard label="Loading tailored resume" />
      </PageState>
    );
  }
  if (run.isError && run.error instanceof ApiError && run.error.isNotFound) {
    return <PageState heading="Tailored resume">{notFound}</PageState>;
  }
  if (run.isError) {
    return (
      <PageState heading="Tailored resume">
        <ApiErrorAlert error={run.error} onRetry={() => void run.refetch()} />
      </PageState>
    );
  }

  const data = run.data;
  const result = data.result;
  const jobName = data.job_title ?? "Untitled job";

  // Another attempt is at THIS run's inputs: the same version, and the match it
  // was informed by. A bare link to the tailor page reset both — the picker to
  // "current", the match to none — so "Try again" on a version 1 resume quietly
  // generated from version 2.
  const again = `/jobs/${data.job_id}/tailor?${new URLSearchParams({
    version: String(data.profile_version_id),
    ...(data.match_result_id ? { match: String(data.match_result_id) } : {}),
  })}`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold">
            <span className="truncate">{jobName}</span>
            <TailoringStatusBadge run={data} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {data.company_name ?? "Unknown company"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link href={`/jobs/${data.job_id}`}>View job</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href={again}>Generate again</Link>
          </Button>
        </div>
      </div>

      {/* Which profile this document is made of. Stated before anything it
          says, because a resume the user cannot trace to a specific confirmed
          snapshot is a resume they cannot check. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Where this came from</CardTitle>
          <CardDescription>
            Generated from an immutable confirmed version, so what it was built on cannot
            have changed since.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <Fact label="Profile version">
              {data.profile_version_number ? (
                <Link
                  className="underline underline-offset-2"
                  href={`/profile/versions/${data.profile_version_number}`}
                >
                  Version {data.profile_version_number}
                </Link>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Target job">
              <Link className="underline underline-offset-2" href={`/jobs/${data.job_id}`}>
                {jobName}
              </Link>
            </Fact>
            <Fact label="Written by">
              {data.generator === "deterministic"
                ? "EasyApply, by selecting your own wording"
                : `A model${data.model ? ` (${data.model})` : ""}`}
            </Fact>
            <Fact label="Generated">{formatDateTime(data.created_at)}</Fact>
            {data.match_result_id ? (
              <Fact label="Informed by">
                {/* The match it was actually informed by. The match page on
                    its own shows the selected version's newest match, which
                    need not be this one. */}
                <Link
                  className="underline underline-offset-2"
                  href={`/jobs/${data.job_id}/match?match=${data.match_result_id}`}
                >
                  Your match for this job
                </Link>
              </Fact>
            ) : null}
            {data.attempt > 1 ? <Fact label="Attempt">{data.attempt}</Fact> : null}
          </dl>
        </CardContent>
      </Card>

      {isTailoring(data) && !data.is_stalled ? (
        <Card>
          <CardContent className="p-6">
            <p role="status" className="text-sm">
              {tailoringStatusLabel(data.status)}…
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              This page updates itself while the work runs.
            </p>
          </CardContent>
        </Card>
      ) : null}

      {isTailoring(data) && data.is_stalled ? (
        <Alert variant="destructive">
          <AlertTitle>This generation stopped responding</AlertTitle>
          <AlertDescription>
            <p>Nothing was saved. Generating again is safe.</p>
            <div className="mt-3">
              <Button variant="outline" size="sm" asChild>
                <Link href={again}>Generate again</Link>
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {data.status === "failed" ? (
        <>
          <Alert variant="destructive">
            <AlertTitle>{tailoringFailureTitle(data)}</AlertTitle>
            <AlertDescription>
              <p>{tailoringFailureExplanation(data)}</p>
              {data.error_code ? (
                <p className="mt-2 text-xs opacity-70">Reference: {data.error_code}</p>
              ) : null}
            </AlertDescription>
          </Alert>
          {/* Whether a retry is worth offering is the server's judgement, not a
              rule re-derived here from the error code — two copies of that rule
              would be two answers free to disagree. */}
          {data.can_retry ? (
            <Button asChild>
              <Link href={again}>Try again</Link>
            </Button>
          ) : (
            <p className="text-sm text-muted-foreground">
              Another attempt would fail the same way without a change to your profile or
              to this deployment&rsquo;s configuration.
            </p>
          )}
        </>
      ) : null}

      {result ? (
        <>
          <GroundingSummary result={result} />

          {isDownloadable(data) && data.artifact ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Download</CardTitle>
                <CardDescription>
                  {data.artifact.filename} · {Math.max(1, Math.round(data.artifact.byte_size / 1024))} KB
                  · Word document
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Button
                  onClick={() => download.mutate(data.id)}
                  disabled={download.isPending}
                >
                  {download.isPending ? "Preparing…" : "Download resume"}
                </Button>
                {download.isError ? <ApiErrorAlert error={download.error} /> : null}
              </CardContent>
            </Card>
          ) : null}

          <TailoredResumeView document={result.document} />
        </>
      ) : null}

      {data.status === "completed" && !result ? (
        <EmptyState
          title="This attempt produced nothing"
          description="The run finished without writing a document. Generating again is safe."
        />
      ) : null}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}
