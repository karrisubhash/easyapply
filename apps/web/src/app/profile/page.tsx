"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { SnapshotView } from "@/components/profile/snapshot-view";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import {
  useCreateDraftVersion,
  useCreateProfile,
  useCurrentVersion,
  useProfile,
  useVersions,
} from "@/lib/api/queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function ProfilePage() {
  const router = useRouter();
  const profile = useProfile();
  const hasProfile = profile.isSuccess;
  const current = useCurrentVersion(hasProfile);
  // Only to tell "nothing confirmed" from "confirmed, but none current".
  const versions = useVersions(hasProfile);
  const createProfile = useCreateProfile();
  const createDraft = useCreateDraftVersion();

  usePageTitle("My Profile");

  // A new draft is somewhere to go, so go there. The button used to create the
  // draft and leave the page exactly as it was — nothing visible happened, so
  // people pressed it again, and every press used up a version number.
  const startDraft = () =>
    createDraft.mutate(undefined, {
      onSuccess: (draft) => router.push(`/profile/versions/${draft.version_number}/edit`),
    });

  if (profile.isPending) {
    return (
      <PageState heading="My Profile">
        <LoadingCard label="Loading profile" />
      </PageState>
    );
  }

  // A 404 here is the first-run empty state, not a failure.
  if (profile.isError && profile.error instanceof ApiError && profile.error.isNotFound) {
    // Its own heading rather than "My Profile": there is no profile yet, and
    // the page's one job in this state is to create it.
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">Create your profile</h1>
        {createProfile.isError ? <ApiErrorAlert error={createProfile.error} /> : null}
        <EmptyState
          title="You do not have a profile yet"
          description="A profile is the anchor your career versions hang off. Create one to get started."
          action={
            <Button onClick={() => createProfile.mutate()} disabled={createProfile.isPending}>
              {createProfile.isPending ? "Creating…" : "Create profile"}
            </Button>
          }
        />
      </div>
    );
  }

  if (profile.isError) {
    return (
      <PageState heading="My Profile">
        <ApiErrorAlert error={profile.error} onRetry={() => void profile.refetch()} />
      </PageState>
    );
  }

  const noCurrentVersion =
    current.isError && current.error instanceof ApiError && current.error.isNotFound;
  const newestConfirmed = [...(versions.data ?? [])]
    .reverse()
    .find((version) => version.status === "confirmed");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">My Profile</h1>
          <p className="text-sm text-muted-foreground">
            Created {formatDateTime(profile.data.created_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" asChild>
            <Link href="/profile/versions">Version History</Link>
          </Button>
          <Button onClick={startDraft} disabled={createDraft.isPending}>
            {createDraft.isPending ? "Creating…" : "New draft"}
          </Button>
        </div>
      </div>

      {createDraft.isError ? <ApiErrorAlert error={createDraft.error} /> : null}

      {current.isPending && !noCurrentVersion ? <LoadingCard label="Loading current version" /> : null}

      {noCurrentVersion && newestConfirmed ? (
        // Confirmed, but confirmed with "Make this the current version"
        // unticked. Calling that "nothing confirmed" sent people to confirm
        // again; the version's own page is where it can be made current.
        <EmptyState
          title="No current version"
          description={`Version ${newestConfirmed.version_number} is confirmed, but no version is marked current, so there is no current profile to show here. Matching and tailoring still work: each names the version it uses. Open a confirmed version to make it current.`}
          action={
            <Button variant="outline" asChild>
              <Link href={`/profile/versions/${newestConfirmed.version_number}`}>
                View version {newestConfirmed.version_number}
              </Link>
            </Button>
          }
        />
      ) : noCurrentVersion && versions.isPending ? (
        <LoadingCard label="Loading versions" />
      ) : noCurrentVersion && versions.isError ? (
        // A list that could not be read says nothing about what is confirmed.
        <ApiErrorAlert error={versions.error} onRetry={() => void versions.refetch()} />
      ) : noCurrentVersion ? (
        <EmptyState
          title="No confirmed version yet"
          description="Create a draft, fill in your career data, review it, then confirm it. Only a confirmed version becomes the one EasyApply uses."
          action={
            <Button onClick={startDraft} disabled={createDraft.isPending}>
              Start a draft
            </Button>
          }
        />
      ) : null}

      {current.isError && !noCurrentVersion ? (
        <ApiErrorAlert error={current.error} onRetry={() => void current.refetch()} />
      ) : null}

      {current.isSuccess ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Current Profile</CardTitle>
              <CardDescription>
                Confirmed {formatDateTime(current.data.confirmed_at)}
              </CardDescription>
            </CardHeader>
          </Card>

          <SnapshotView snapshot={current.data.snapshot} />
        </>
      ) : null}
    </div>
  );
}
