"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { CurrentBadge, StatusBadge } from "@/components/profile/status-badge";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useCreateDraftVersion, useProfile, useVersions } from "@/lib/api/queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

export default function VersionsPage() {
  const profile = useProfile();
  const versions = useVersions(profile.isSuccess);
  const createDraft = useCreateDraftVersion();
  const router = useRouter();

  usePageTitle("Version History");

  // The same as the profile page's New draft: a new draft is somewhere to go.
  // Staying put left people unsure it had worked, and each extra press used up
  // a version number.
  const startDraft = () =>
    createDraft.mutate(undefined, {
      onSuccess: (draft) => router.push(`/profile/versions/${draft.version_number}/edit`),
    });

  if (profile.isPending) {
    return (
      <PageState heading="Version History">
        <LoadingCard label="Loading profile" />
      </PageState>
    );
  }

  if (profile.isError && profile.error instanceof ApiError && profile.error.isNotFound) {
    return (
      <PageState heading="Version History">
        <EmptyState
          title="No profile yet"
          description="Create a profile before adding versions."
          action={
            <Button asChild>
              <Link href="/profile">Go to profile</Link>
            </Button>
          }
        />
      </PageState>
    );
  }

  if (profile.isError) {
    return (
      <PageState heading="Version History">
        <ApiErrorAlert error={profile.error} onRetry={() => void profile.refetch()} />
      </PageState>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Version History</h1>
          <p className="text-sm text-muted-foreground">
            Every confirmed version is kept forever for audit and reproducibility, so anything
            generated from it can still be explained. Matching and tailoring start from your
            current version, and on either page you can pick any other confirmed version
            instead.
          </p>
        </div>
        <Button onClick={startDraft} disabled={createDraft.isPending}>
          {createDraft.isPending ? "Creating…" : "New draft"}
        </Button>
      </div>

      {createDraft.isError ? <ApiErrorAlert error={createDraft.error} /> : null}
      {versions.isPending ? <LoadingCard label="Loading versions" /> : null}
      {versions.isError ? (
        <ApiErrorAlert error={versions.error} onRetry={() => void versions.refetch()} />
      ) : null}

      {versions.isSuccess && versions.data.length === 0 ? (
        <EmptyState
          title="No versions yet"
          description="Create your first draft to start recording your career data."
          action={
            <Button onClick={startDraft} disabled={createDraft.isPending}>
              Create a draft
            </Button>
          }
        />
      ) : null}

      {versions.isSuccess && versions.data.length > 0 ? (
        <ul className="space-y-3">
          {[...versions.data].reverse().map((version) => (
            <li key={version.version_number}>
              <Card>
                <CardContent className="flex items-center justify-between gap-4 p-4">
                  <div>
                    <p className="flex items-center gap-2 font-medium">
                      Version {version.version_number}
                      <StatusBadge status={version.status} />
                      {version.is_current && <CurrentBadge />}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Created {formatDateTime(version.created_at)}
                      {version.confirmed_at
                        ? ` · Confirmed ${formatDateTime(version.confirmed_at)}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link
                        href={`/profile/versions/${version.version_number}`}
                        aria-label={`${
                          version.status === "draft" ? "Review" : "View"
                        } version ${version.version_number}`}
                      >
                        {version.status === "draft" ? "Review" : "View"}
                      </Link>
                    </Button>
                    {version.status === "draft" && (
                      <Button size="sm" asChild>
                        <Link
                          href={`/profile/versions/${version.version_number}/edit`}
                          aria-label={`Edit version ${version.version_number}`}
                        >
                          Edit
                        </Link>
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
