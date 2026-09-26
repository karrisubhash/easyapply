"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import * as React from "react";

import { MatchResultView } from "@/components/match/match-result";
import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { NoConfirmedVersion } from "@/components/profile/no-confirmed-version";
import {
  VersionPicker,
  idParam,
  resolveVersion,
  useConfirmedVersions,
} from "@/components/profile/version-picker";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { useJob } from "@/lib/api/job-queries";
import { useCreateMatch, useMatch, useMatches } from "@/lib/api/match-queries";
import { usePageTitle } from "@/lib/use-page-title";

/**
 * What this profile satisfies in this job's requirements.
 *
 * The page answers four questions, in this order, because that is the order
 * they are useful in: what is required and missing, what is uncertain, what
 * matched, and what evidence caused each conclusion. Coverage appears once,
 * beside the counts that explain it — §20 asks that the percentage not
 * dominate, and the reason is that the percentage is the least actionable
 * thing here.
 *
 * Matching is an explicit action rather than something that happens on load.
 * A match is a stored record of a judgment, pinned to a profile version and a
 * requirements row, so creating one is a decision the user makes — and
 * re-requesting is free, since the server returns the existing result for the
 * same inputs rather than a second copy.
 */
export default function JobMatchPage() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const jobId = Number(params.id);

  // `?match=<id>` shows one specific match — the one a tailored resume was
  // informed by, say — rather than whichever this page would pick. `?version=`
  // pre-selects a version, the way the tailor page is linked to.
  const pinnedId = idParam(search?.get("match"));
  const linkedVersionId = idParam(search?.get("version"));

  // The user's own pick in the picker, once they have made one. Until then the
  // selection follows the link (a pinned match's version, or ?version=) and
  // otherwise the default. Wrapped in an object because `undefined` is itself
  // a real choice — "the current version" — and must not read as "no choice".
  const [choice, setChoice] = React.useState<{ value: number | undefined } | null>(null);
  // A pinned match stops being what the page shows once the user moves on from
  // it — picks another version or matches again. Keyed on the id, so following
  // a different ?match= link pins again.
  const [releasedPin, setReleasedPin] = React.useState<number | undefined>(undefined);

  // Hooks run unconditionally, before any early return; each query is disabled
  // for an id that addresses nothing.
  const job = useJob(jobId);
  const matches = useMatches(jobId);
  const createMatch = useCreateMatch(jobId);
  // Asked before the action is offered rather than discovered from its 409:
  // matching without a confirmed version cannot succeed, so the button for it
  // should not be there.
  const versions = useConfirmedVersions();

  // Only a match of THIS job can be pinned here. The id is global, and a link
  // naming another job's match would otherwise put that job's verdicts under
  // this job's title.
  const pinActive = pinnedId !== undefined && releasedPin !== pinnedId;
  const pinned = pinActive ? matches.data?.find((item) => item.id === pinnedId) : undefined;
  const pinUnknown = pinActive && matches.isSuccess && !pinned;

  const pickerValue: number | undefined = choice
    ? choice.value
    : pinned
      ? pinned.profile_version_id
      : linkedVersionId !== undefined &&
          versions.confirmed.some((version) => version.id === linkedVersionId)
        ? linkedVersionId
        : undefined;
  const { version: selected, send } = resolveVersion(pickerValue, versions);

  // Which stored match answers "what did the selected version score?".
  //
  // It used to be `matches.data[0]` — the newest match for the job, whatever
  // version it judged. The list is newest-first and a repeat POST returns the
  // EXISTING row, so after picking version 2 and pressing Match the page went on
  // showing version 3's result, and on load it showed a version 1 match under a
  // picker saying "current (version 2)". Now it is the newest match OF the
  // selected version. Only when the versions cannot be read at all does it fall
  // back to the newest one — that match states its own version, and there is no
  // picker on the page to contradict it.
  const forSelected = selected
    ? matches.data?.find((item) => item.profile_version_id === selected.id)
    : versions.isError
      ? matches.data?.[0]
      : undefined;
  // While a pin is being looked up nothing else is fetched; a pin this job does
  // not have falls back to the selected version's own match.
  const targetId = pinned ? pinned.id : pinActive && !pinUnknown ? undefined : forSelected?.id;
  const match = useMatch(targetId);

  // The POST's answer wins while it stands: it is the result of the button the
  // user just pressed. Cleared (by `reset`) the moment they pick another
  // version, so it is never shown under a version it was not made from.
  const shown = createMatch.data ?? match.data;
  const shownVersionNumber = shown
    ? (shown.profile_version_number ??
      versions.confirmed.find((version) => version.id === shown.profile_version_id)
        ?.version_number)
    : undefined;
  // Some other version's match, for the empty state to point at.
  const otherMatch =
    !shown && selected
      ? matches.data?.find((item) => item.profile_version_id !== selected.id)
      : undefined;

  usePageTitle("Match", job.data?.job_title);

  const pickVersion = (value: number | undefined) => {
    setChoice({ value });
    if (pinActive) setReleasedPin(pinnedId);
    createMatch.reset();
  };

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

  if (!Number.isInteger(jobId) || jobId < 1) {
    return <PageState heading="Match">{notFound}</PageState>;
  }
  if (job.isPending) {
    return (
      <PageState heading="Match">
        <LoadingCard label="Loading job" />
      </PageState>
    );
  }
  if (job.isError && job.error instanceof ApiError && job.error.isNotFound) {
    return <PageState heading="Match">{notFound}</PageState>;
  }
  if (job.isError) {
    return (
      <PageState heading="Match">
        <ApiErrorAlert error={job.error} onRetry={() => void job.refetch()} />
      </PageState>
    );
  }

  const failed = matches.isError || match.isError;
  const loading =
    !createMatch.data &&
    (matches.isPending ||
      (!versions.isError && versions.isPending) ||
      (targetId !== undefined && match.isPending));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold">Match</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {job.data.job_title}
            {job.data.company_name ? ` · ${job.data.company_name}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <Link href={`/jobs/${jobId}`}>Back to job</Link>
          </Button>
          {versions.hasConfirmedVersion ? (
            <Button
              onClick={() => {
                // Freeze the selection the button acted on: releasing the pin
                // must not move the picker to another version underneath the
                // result that is about to arrive.
                setChoice({ value: pickerValue });
                if (pinActive) setReleasedPin(pinnedId);
                createMatch.mutate(send);
              }}
              disabled={createMatch.isPending}
            >
              {createMatch.isPending
                ? "Matching…"
                : shown && selected && shown.profile_version_id === selected.id
                  ? "Match again"
                  : "Match my profile"}
            </Button>
          ) : null}
        </div>
      </div>

      {/* A list that could not be read is not a list with nothing in it. */}
      {versions.isError ? (
        <ApiErrorAlert error={versions.error} onRetry={versions.refetch} />
      ) : null}

      {!versions.isPending && !versions.isError && !versions.hasConfirmedVersion ? (
        <NoConfirmedVersion action="matched" />
      ) : null}

      {/* Renders nothing when there is only one confirmed version, which is the
          common case — a choice between one thing is not a choice. */}
      <VersionPicker value={pickerValue} onChange={pickVersion} label="Match from" />

      {pinUnknown ? (
        <Alert>
          <AlertTitle>That match is not one of this job&rsquo;s</AlertTitle>
          <AlertDescription>
            The link named a match this job does not have, so the page shows this job&rsquo;s own
            match for the version above instead.
          </AlertDescription>
        </Alert>
      ) : null}

      {/* A 409 here is one of two states the user can act on, and the server's
          own sentence says which — so it is shown rather than replaced by a
          rule re-derived on this side. */}
      {createMatch.isError ? <MatchRefusal error={createMatch.error} jobId={jobId} /> : null}

      {matches.isError ? (
        <ApiErrorAlert error={matches.error} onRetry={() => void matches.refetch()} />
      ) : null}
      {match.isError ? (
        <ApiErrorAlert error={match.error} onRetry={() => void match.refetch()} />
      ) : null}

      {loading && !failed ? <LoadingCard label="Loading match" /> : null}

      {/* Only an empty state when the page knows it is empty. Beside a failed
          read it contradicted the alert above it. */}
      {!shown &&
      !loading &&
      !failed &&
      !createMatch.isPending &&
      !createMatch.isError &&
      versions.hasConfirmedVersion ? (
        <EmptyState
          title={
            otherMatch && selected
              ? `Version ${selected.version_number} has not been matched against this job yet`
              : "This job has not been matched yet"
          }
          description={
            otherMatch
              ? `There is a match from ${versionName(otherMatch.profile_version_number)}, but a match judges one version and says nothing about another. Match to see how this version compares.`
              : "Matching compares a confirmed profile version against this posting's structured requirements, and shows the evidence behind every conclusion."
          }
          action={
            otherMatch ? (
              <Button variant="outline" asChild>
                <Link
                  href={`/jobs/${jobId}/match?match=${otherMatch.id}`}
                  // The page is not remounted by a search-param change, so an
                  // earlier pick or released pin would outlive the link — and
                  // when the URL is already this one, nothing would change.
                  onClick={() => {
                    setChoice(null);
                    setReleasedPin(undefined);
                    createMatch.reset();
                  }}
                >
                  View the match from {versionName(otherMatch.profile_version_number)}
                </Link>
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {shown ? (
        <>
          {selected && shown.profile_version_id !== selected.id ? (
            <Alert variant="warning">
              <AlertTitle>This match is for a different version</AlertTitle>
              <AlertDescription>
                It judged {versionName(shownVersionNumber)}, not version{" "}
                {selected.version_number} selected above. Match again to judge version{" "}
                {selected.version_number}.
              </AlertDescription>
            </Alert>
          ) : versions.current && shown.profile_version_id !== versions.current.id ? (
            <p className="text-sm text-muted-foreground">
              This match judged {versionName(shownVersionNumber)}, which is not your current
              version (version {versions.current.version_number}).
            </p>
          ) : null}
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
              <p className="text-sm text-muted-foreground">
                Generate a resume for this job that leads with what these requirements ask
                for. It will state nothing this match could not evidence.
              </p>
              <Button asChild>
                {/* The version travels with the match. A match judged one
                    version; the server refuses to tailor another from it, and
                    without this the tailor page defaulted to "current". */}
                <Link
                  href={`/jobs/${jobId}/tailor?version=${shown.profile_version_id}&match=${shown.id}`}
                >
                  Tailor resume
                </Link>
              </Button>
            </CardContent>
          </Card>
          <MatchResultView match={shown} />
        </>
      ) : null}
    </div>
  );
}

function versionName(versionNumber: number | null | undefined): string {
  return versionNumber ? `version ${versionNumber}` : "another version";
}

/**
 * The two refusals a user can do something about, told apart by their code.
 *
 * Both are 409s and both carry the server's own explanation, which is what is
 * shown; only the suggested next step differs, and that is a navigation
 * decision rather than a judgment about the data.
 */
function MatchRefusal({ error, jobId }: { error: unknown; jobId: number }) {
  if (!(error instanceof ApiError) || !error.isConflict) {
    return <ApiErrorAlert error={error} />;
  }

  const aboutProfile = /confirm|draft/i.test(error.message);

  return (
    <Alert>
      <AlertTitle>This job cannot be matched yet</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <div className="mt-3">
          {aboutProfile ? (
            <Button variant="outline" size="sm" asChild>
              <Link href="/profile">Go to profile</Link>
            </Button>
          ) : (
            <Button variant="outline" size="sm" asChild>
              <Link href={`/jobs/${jobId}`}>Go to job</Link>
            </Button>
          )}
        </div>
      </AlertDescription>
    </Alert>
  );
}
