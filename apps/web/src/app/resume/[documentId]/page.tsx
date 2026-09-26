"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { CandidateReview } from "@/components/resume/candidate-review";
import {
  ProcessingStatusBadge,
  failureExplanation,
  failureTitle,
  isQueued,
  isWaitingToRetry,
  statusLabel,
} from "@/components/resume/processing-status";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useVersions } from "@/lib/api/queries";
import { isProcessing, useReprocessResume, useResume, useResumeResult } from "@/lib/api/resume-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function ResumeDetailPage() {
  const params = useParams<{ documentId: string }>();
  const documentId = Number(params.documentId);

  const document = useResume(documentId);
  const run = document.data?.latest_run ?? null;
  const isDone = run?.status === "completed";
  const result = useResumeResult(documentId, isDone);
  const reprocess = useReprocessResume(documentId);

  const versionNumber = result.data?.result?.profile_version_number ?? null;
  // What became of the draft this document produced. The card below said
  // "Draft version N is ready… Nothing here is part of your profile yet" for
  // ever, including after version N had been confirmed — and offered to edit a
  // version that can no longer be edited.
  const versions = useVersions(versionNumber !== null);
  const producedVersion = versions.data?.find(
    (version) => version.version_number === versionNumber,
  );

  usePageTitle(document.data?.original_filename ?? "Resume");

  const notFound = (
    <EmptyState
      title="That resume does not exist"
      description="It may have been removed, or the link may be wrong."
      action={
        <Button variant="outline" asChild>
          <Link href="/resume">Back to resumes</Link>
        </Button>
      }
    />
  );

  // A document id that is not a number addresses nothing, and useResume leaves
  // the query disabled — which would otherwise render as a permanent skeleton.
  if (!Number.isInteger(documentId) || documentId < 1) {
    return <PageState heading="Resume">{notFound}</PageState>;
  }

  if (document.isPending) {
    return (
      <PageState heading="Resume">
        <LoadingCard label="Loading resume" />
      </PageState>
    );
  }

  if (document.isError && document.error instanceof ApiError && document.error.isNotFound) {
    return <PageState heading="Resume">{notFound}</PageState>;
  }

  if (document.isError) {
    return (
      <PageState heading="Resume">
        <ApiErrorAlert error={document.error} onRetry={() => void document.refetch()} />
      </PageState>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <span className="truncate">{document.data.original_filename}</span>
            <ProcessingStatusBadge run={run} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Uploaded {formatDateTime(document.data.created_at)}
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href="/resume">All resumes</Link>
        </Button>
      </div>

      {isProcessing(document.data) ? (
        <Card>
          <CardContent className="p-6">
            <p role="status" className="text-sm">
              {isWaitingToRetry(run)
                ? "Waiting to try again…"
                : `${statusLabel(run!.status)}…`}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {isWaitingToRetry(run)
                ? "The last attempt hit a temporary problem. We are waiting a moment before trying again — you do not need to do anything."
                : isQueued(run)
                  ? "Waiting for a worker to pick this up. You can leave this page; processing continues without it."
                  : "This page updates itself while the work runs."}
            </p>
            {run!.attempt > 1 ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Attempt {run!.attempt}.
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {run?.status === "failed" ? (
        <>
          <Alert variant="destructive">
            <AlertTitle>{failureTitle(run)}</AlertTitle>
            <AlertDescription>
              <p>{failureExplanation(run)}</p>
              {run.attempt > 1 ? (
                <p className="mt-2">This was attempt {run.attempt}.</p>
              ) : null}
              {run.error_code ? (
                <p className="mt-2 text-xs opacity-70">Reference: {run.error_code}</p>
              ) : null}
            </AlertDescription>
          </Alert>
          {/* Whether a retry is worth offering is the server's judgement, not a
              rule re-derived here from the error code — two copies of that rule
              would be two answers free to disagree. */}
          {run.can_retry ? (
            <div className="flex items-center gap-2">
              <Button onClick={() => reprocess.mutate()} disabled={reprocess.isPending}>
                {reprocess.isPending ? "Retrying…" : "Try again"}
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Another attempt would fail the same way. Upload a corrected file instead.
            </p>
          )}
          {reprocess.isError ? <ApiErrorAlert error={reprocess.error} /> : null}
        </>
      ) : null}

      {isDone && versionNumber && producedVersion?.status === "confirmed" ? (
        <Card>
          <CardHeader>
            <CardTitle>Confirmed as version {versionNumber}</CardTitle>
            <CardDescription>
              What this resume said is part of your profile now, as it stood when you confirmed
              it. Confirmed versions cannot be edited; a new draft is how to change it.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Button variant="outline" asChild>
              <Link href={`/profile/versions/${versionNumber}`}>View version {versionNumber}</Link>
            </Button>
          </CardContent>
        </Card>
      ) : isDone && versionNumber ? (
        <Card>
          <CardHeader>
            <CardTitle>Draft version {versionNumber} is ready</CardTitle>
            <CardDescription>
              Nothing here is part of your profile yet. Review it, edit anything that is wrong, then
              confirm it.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Button asChild>
              <Link href={`/profile/versions/${versionNumber}/edit`}>Edit the draft</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link href={`/profile/versions/${versionNumber}`}>Review and confirm</Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}

      {result.isPending && isDone ? <LoadingCard label="Loading result" /> : null}
      {result.isError ? (
        <ApiErrorAlert error={result.error} onRetry={() => void result.refetch()} />
      ) : null}

      {result.data?.result ? (
        <>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>
              Interpreted by {result.data.result.structurer}
              {result.data.result.model ? ` (${result.data.result.model})` : ""}
            </span>
            <span>·</span>
            <span>
              {result.data.run.page_count} page
              {result.data.run.page_count === 1 ? "" : "s"} read
              {result.data.run.ocr_page_count ? `, ${result.data.run.ocr_page_count} scanned` : ""}
            </span>
          </div>
          <CandidateReview result={result.data.result} />
        </>
      ) : null}
    </div>
  );
}
