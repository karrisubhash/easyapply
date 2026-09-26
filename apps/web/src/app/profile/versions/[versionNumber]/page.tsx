"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { ConfirmVersionButton } from "@/components/profile/confirm-version-button";
import { SnapshotView } from "@/components/profile/snapshot-view";
import { CurrentBadge, StatusBadge } from "@/components/profile/status-badge";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { useMakeVersionCurrent, useVersion } from "@/lib/api/queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function VersionPage() {
  const params = useParams<{ versionNumber: string }>();
  const versionNumber = Number(params.versionNumber);
  const makeCurrent = useMakeVersionCurrent(versionNumber);
  const version = useVersion(versionNumber);
  const valid = Number.isInteger(versionNumber) && versionNumber >= 1;
  const heading = valid ? `Version ${versionNumber}` : "Profile version";

  usePageTitle(heading);

  const notFound = (
    <EmptyState
      title={`Version ${params.versionNumber} does not exist`}
      description="It may have been removed, or the number may be wrong."
      action={
        <Button variant="outline" asChild>
          <Link href="/profile/versions">Back to versions</Link>
        </Button>
      }
    />
  );

  // A version number is the whole address here, so a segment that is not one
  // addresses nothing. useVersion leaves that query disabled, which without this
  // check renders as a loading skeleton that can never resolve.
  if (!valid) return <PageState heading={heading}>{notFound}</PageState>;

  if (version.isPending) {
    return (
      <PageState heading={heading}>
        <LoadingCard label="Loading version" />
      </PageState>
    );
  }

  if (version.isError && version.error instanceof ApiError && version.error.isNotFound) {
    return <PageState heading={heading}>{notFound}</PageState>;
  }

  if (version.isError) {
    return (
      <PageState heading={heading}>
        <ApiErrorAlert error={version.error} onRetry={() => void version.refetch()} />
      </PageState>
    );
  }

  const isDraft = version.data.status === "draft";

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            Version {version.data.version_number}
            <StatusBadge status={version.data.status} />
            {version.data.is_current && <CurrentBadge />}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Created {formatDateTime(version.data.created_at)}
            {version.data.confirmed_at
              ? ` · Confirmed ${formatDateTime(version.data.confirmed_at)}`
              : ""}
          </p>
        </div>

        {isDraft ? (
          <div className="flex gap-2">
            <Button variant="outline" asChild>
              <Link href={`/profile/versions/${versionNumber}/edit`}>Edit</Link>
            </Button>
            <ConfirmVersionButton versionNumber={versionNumber} />
          </div>
        ) : !version.data.is_current ? (
          // A confirmed version that is not current — confirmed with "make
          // current" unticked, or superseded. Promoting it changes which
          // version the profile page shows and matching and tailoring start
          // from; the snapshot itself is untouched.
          <Button onClick={() => makeCurrent.mutate()} disabled={makeCurrent.isPending}>
            {makeCurrent.isPending ? "Making current…" : "Make this the current version"}
          </Button>
        ) : null}
      </div>

      {makeCurrent.isError ? <ApiErrorAlert error={makeCurrent.error} /> : null}

      {isDraft ? (
        <Alert variant="warning">
          <AlertTitle>This is a draft</AlertTitle>
          <AlertDescription>
            Nothing here is authoritative yet. Review it, then confirm — after that it is frozen
            permanently, and anything EasyApply generates from it will point back to this exact
            version.
          </AlertDescription>
        </Alert>
      ) : (
        <Alert>
          <AlertTitle>This version is confirmed</AlertTitle>
          <AlertDescription>
            Confirmed versions cannot be edited. To change your career data, create a new draft.
          </AlertDescription>
        </Alert>
      )}

      <SnapshotView snapshot={version.data.snapshot} />
    </div>
  );
}
