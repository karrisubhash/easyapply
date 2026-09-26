"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { SnapshotEditor } from "@/components/profile/snapshot-editor";
import {
  toSnapshotInput,
  type SnapshotFormValues,
} from "@/components/profile/snapshot-form-values";
import { StatusBadge } from "@/components/profile/status-badge";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { useSaveSnapshot, useVersion } from "@/lib/api/queries";
import { usePageTitle } from "@/lib/use-page-title";

export default function EditVersionPage() {
  const params = useParams<{ versionNumber: string }>();
  const router = useRouter();
  const versionNumber = Number(params.versionNumber);

  const version = useVersion(versionNumber);
  const save = useSaveSnapshot(versionNumber);
  const valid = Number.isInteger(versionNumber) && versionNumber >= 1;
  // Not "Editing version N" until the editor is actually on the page: that
  // heading is the signal that the form is there to fill in, and a loading or
  // refusing page is not that.
  const heading = valid ? `Version ${versionNumber}` : "Profile version";

  usePageTitle(
    version.data?.status === "draft" ? `Editing version ${versionNumber}` : heading,
  );

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

  // See the review page: a segment that is not a version number addresses
  // nothing, and the query it would drive never runs.
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

  // A confirmed version is not editable. The API would refuse the write anyway;
  // this is so the user is told before filling in a form that cannot be saved.
  if (version.data.status === "confirmed") {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">{heading}</h1>
        <Alert>
          <AlertTitle>Version {versionNumber} is confirmed</AlertTitle>
          <AlertDescription>
            Confirmed versions are permanent and cannot be edited. Create a new draft to record
            changes to your career data.
          </AlertDescription>
        </Alert>
        <Button variant="outline" asChild>
          <Link href={`/profile/versions/${versionNumber}`}>View this version</Link>
        </Button>
      </div>
    );
  }

  const handleSave = (values: SnapshotFormValues) => {
    save.mutate(toSnapshotInput(values), {
      onSuccess: () => router.push(`/profile/versions/${versionNumber}`),
    });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          Editing version {versionNumber}
          <StatusBadge status={version.data.status} />
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Saving replaces the whole snapshot for this draft. Nothing is authoritative until you
          confirm it.
        </p>
      </div>

      <SnapshotEditor
        snapshot={version.data.snapshot}
        onSave={handleSave}
        isSaving={save.isPending}
        saveError={save.error}
        secondaryAction={
          <Button type="button" variant="outline" asChild>
            <Link href={`/profile/versions/${versionNumber}`}>Review without saving</Link>
          </Button>
        }
      />
    </div>
  );
}
