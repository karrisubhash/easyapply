"use client";

import Link from "next/link";

import { JobStatusBadge } from "@/components/job/job-status-badge";
import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { LoadingCard } from "@/components/profile/states";
import { CurrentBadge } from "@/components/profile/status-badge";
import { ProcessingStatusBadge } from "@/components/resume/processing-status";
import { TailoringStatusBadge } from "@/components/tailoring/tailoring-status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useJobs } from "@/lib/api/job-queries";
import { useCurrentVersion, useProfile, useVersions } from "@/lib/api/queries";
import { useResumes } from "@/lib/api/resume-queries";
import { useTailoredResumes } from "@/lib/api/tailoring-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * The dashboard.
 *
 * Every number on this page is a fact the API returned. There are no
 * percentages, no trends, no "profile strength", and no counters of things the
 * user has not done — a job-application product that opens on invented
 * analytics is teaching its user not to believe the parts that matter, and the
 * parts that matter here are a grounding verdict and a match verdict.
 *
 * What it shows instead is STATE and the next useful action for that state: is
 * there a confirmed profile, did the last resume finish reading, what was
 * saved and matched recently, what has been generated. Each card is empty
 * exactly when the thing it describes does not exist yet, and an empty card
 * carries the action that would fill it.
 */
export default function DashboardPage() {
  const profile = useProfile();
  const current = useCurrentVersion(profile.isSuccess);
  const resumes = useResumes();
  const jobs = useJobs();
  const tailored = useTailoredResumes();
  // Only to tell "nothing confirmed" from "confirmed, but none current" — a
  // version confirmed with "Make this the current version" unticked leaves the
  // second, and the card used to call it the first.
  const versions = useVersions(profile.isSuccess);

  usePageTitle("Dashboard");

  const noProfile =
    profile.isError && profile.error instanceof ApiError && profile.error.isNotFound;
  const noCurrentVersion =
    current.isError && current.error instanceof ApiError && current.error.isNotFound;
  // A 404 from the current-version query is an answer ("nothing is confirmed
  // yet"). Any OTHER failure is a failure, and it needs its own branch: without
  // one the card fell through to the loading sentence on a settled query and
  // sat there forever, which is exactly the permanent fake spinner §11 forbids.
  const currentFailed = current.isError && !noCurrentVersion;
  // And the other way the same sentence got stuck. `useCurrentVersion` is
  // disabled until the profile query succeeds, so when THAT one fails the
  // current-version query is never dispatched: it stays `isPending` with
  // nothing in flight, forever. With no branch of its own it fell through to
  // the loading sentence below — a spinner for a request that will never be
  // made, sitting directly above an alert explaining that the API is down.
  const profileFailed = profile.isError && !noProfile;
  const noneCurrent =
    noCurrentVersion && (versions.data ?? []).some((version) => version.status === "confirmed");
  // With no current version, only the list can say whether anything is
  // confirmed; a list that failed must not be read as an empty one.
  const versionsFailed = noCurrentVersion && versions.isError;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload a resume, confirm what it says about you, then tailor it to a job.
        </p>
      </div>

      {/* The first thing that has to be true. Everything downstream — matching,
          tailoring — needs a confirmed version, so the dashboard leads with
          whether one exists rather than making the user discover it from a 409
          two pages later. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Profile
            {current.isSuccess ? <CurrentBadge /> : null}
          </CardTitle>
          <CardDescription>
            {profile.isPending
              ? "Checking…"
              : noProfile
                ? "You do not have a profile yet."
                : profileFailed
                  ? "Your profile could not be loaded."
                  : noneCurrent
                    ? "You have confirmed versions, but none is marked current. Matching and tailoring still work: each names the version it uses."
                    : noCurrentVersion && versions.isPending
                      ? "Checking…"
                      : versionsFailed
                        ? "Your versions could not be loaded."
                      : noCurrentVersion
                        ? "No version has been confirmed yet. Only a confirmed version can be matched or tailored."
                    : currentFailed
                      ? "Your current version could not be loaded."
                      : current.isSuccess
                        ? `Confirmed ${formatDateTime(current.data.confirmed_at)}.`
                        : "Loading your current version…"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {noneCurrent ? (
            <Button asChild size="sm" variant="outline">
              <Link href="/profile/versions">Version history</Link>
            </Button>
          ) : noProfile || (noCurrentVersion && !versionsFailed) ? (
            <Button asChild size="sm">
              <Link href="/profile">{noProfile ? "Create profile" : "Review and confirm"}</Link>
            </Button>
          ) : (
            <Button asChild size="sm" variant="outline">
              <Link href="/profile">View profile</Link>
            </Button>
          )}
          <Button asChild size="sm" variant="outline">
            <Link href="/resume">Upload a resume</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/jobs/new">Add a job</Link>
          </Button>
        </CardContent>
      </Card>

      {profileFailed ? (
        <ApiErrorAlert error={profile.error} onRetry={() => void profile.refetch()} />
      ) : null}
      {versionsFailed ? (
        <ApiErrorAlert error={versions.error} onRetry={() => void versions.refetch()} />
      ) : null}
      {currentFailed ? (
        <ApiErrorAlert error={current.error} onRetry={() => void current.refetch()} />
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <RecentCard
          title="Resumes"
          description="The files EasyApply has read for you."
          query={resumes}
          href="/resume"
          emptyAction={{ href: "/resume", label: "Upload a resume" }}
          emptyText="No resumes uploaded yet."
          render={(document) => (
            <Row
              key={document.id}
              href={`/resume/${document.id}`}
              title={document.original_filename}
              meta={`Uploaded ${formatDateTime(document.created_at)}`}
              badge={<ProcessingStatusBadge run={document.latest_run} />}
            />
          )}
        />

        <RecentCard
          title="Jobs"
          description="Postings saved and read into structured requirements."
          query={jobs}
          href="/jobs"
          emptyAction={{ href: "/jobs/new", label: "Add a job" }}
          emptyText="No jobs saved yet."
          render={(job) => (
            <Row
              key={job.id}
              href={`/jobs/${job.id}`}
              title={job.job_title}
              meta={job.company_name ?? "Unknown company"}
              badge={<JobStatusBadge run={job.latest_run} />}
            />
          )}
        />
      </div>

      <RecentCard
        title="Tailored resumes"
        description="Documents generated from a confirmed version for one job."
        query={tailored}
        href="/tailored-resumes"
        emptyAction={{ href: "/jobs", label: "Choose a job to tailor for" }}
        emptyText="Nothing generated yet. Open a job and choose Tailor resume."
        render={(run) => (
          <Row
            key={run.id}
            href={`/tailored-resumes/${run.id}`}
            title={run.job_title ?? "Untitled job"}
            meta={[
              run.company_name,
              run.profile_version_number ? `From version ${run.profile_version_number}` : null,
              formatDateTime(run.created_at),
            ]
              .filter(Boolean)
              .join(" · ")}
            badge={<TailoringStatusBadge run={run} />}
          />
        )}
      />
    </div>
  );
}

/** How many of a collection the dashboard shows. Enough to recognise recent
 *  work, few enough that the page stays a summary rather than a second listing. */
const RECENT = 3;

interface RecentCardProps<T> {
  title: string;
  description: string;
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data?: T[];
    refetch: () => unknown;
  };
  href: string;
  emptyText: string;
  emptyAction: { href: string; label: string };
  render: (item: T) => React.ReactNode;
}

/**
 * One collection, in whichever of its four states it is actually in.
 *
 * Shared rather than repeated three times so that loading, empty, error and
 * populated cannot drift apart between cards — §11 asks for all four
 * everywhere, and the reliable way to get that is for there to be one place
 * they are written.
 */
function RecentCard<T>({
  title,
  description,
  query,
  href,
  emptyText,
  emptyAction,
  render,
}: RecentCardProps<T>) {
  const items = query.data ?? [];

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="min-w-0">
          <CardTitle className="text-base">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        {items.length > 0 ? (
          <Button asChild size="sm" variant="ghost">
            {/* Three of these on one page. Read as a list of links they were
                three identical "View all"s. */}
            <Link href={href} aria-label={`View all ${title.toLowerCase()}`}>
              View all
            </Link>
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {query.isPending ? <LoadingCard label={`Loading ${title.toLowerCase()}`} /> : null}
        {query.isError ? <ApiErrorAlert error={query.error} onRetry={query.refetch} /> : null}

        {!query.isPending && !query.isError && items.length === 0 ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">{emptyText}</p>
            <Button asChild size="sm" variant="outline">
              <Link href={emptyAction.href}>{emptyAction.label}</Link>
            </Button>
          </div>
        ) : null}

        {items.length > 0 ? (
          <ul className="divide-y">{items.slice(0, RECENT).map(render)}</ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Row({
  href,
  title,
  meta,
  badge,
}: {
  href: string;
  title: string;
  meta: string;
  badge?: React.ReactNode;
}) {
  return (
    <li>
      <Link
        href={href}
        className="flex items-center justify-between gap-3 py-3 hover:opacity-80"
      >
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{title}</span>
          <span className="block truncate text-xs text-muted-foreground">{meta}</span>
        </span>
        {badge}
      </Link>
    </li>
  );
}
