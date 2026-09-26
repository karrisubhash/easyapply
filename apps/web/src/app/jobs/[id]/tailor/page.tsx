"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { ApiErrorAlert } from "@/components/profile/api-error-alert";
import { EmptyState, LoadingCard, PageState } from "@/components/profile/states";
import { NoConfirmedVersion } from "@/components/profile/no-confirmed-version";
import {
  VersionPicker,
  idParam,
  resolveVersion,
  useConfirmedVersions,
} from "@/components/profile/version-picker";
import {
  TailoringStatusBadge,
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
import { useJob, useJobRequirements } from "@/lib/api/job-queries";
import { useMatch } from "@/lib/api/match-queries";
import {
  isTailoring,
  useStartTailoring,
  useTailoredResume,
  useTailoredResumesForJob,
} from "@/lib/api/tailoring-queries";
import { usePageTitle } from "@/lib/use-page-title";
import { formatDateTime } from "@/lib/utils";

/**
 * Generate a resume for this job.
 *
 * The page is a decision, an action, and a wait — in that order, and it never
 * pretends any of the three has happened before it has. Generation runs on a
 * worker, so pressing Generate produces a QUEUED run and this page polls it;
 * the states it shows are the states the server is actually in, not a spinner
 * standing in for all of them.
 *
 * Which profile version was used is stated on every screen here, before
 * generation and after it. A tailored resume that the user cannot trace to a
 * specific confirmed snapshot is a document they cannot check, and §8 asks for
 * exactly this: the user must know which profile was used.
 */
export default function TailorJobPage() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const jobId = Number(params.id);

  // Carried from the match page when the user arrived from there, together
  // with the version that match judged. It only ever changes what the generator
  // leads with — see tailoring/pipeline.py::_emphasis for why a match cannot
  // make a document claim anything.
  const fromMatch = idParam(search?.get("match"));
  const linkedVersionId = idParam(search?.get("version"));

  // The user's own pick, once they make one; until then the page follows the
  // link. An object because `undefined` ("the current version") is a real
  // choice and must not read as "no choice yet".
  const [choice, setChoice] = React.useState<{ value: number | undefined } | null>(null);

  const job = useJob(jobId);
  // A failed REPROCESS leaves the previous reading in force, and the server
  // tailors against it (tailoring/service.py::_load_requirements). Gating on the
  // latest attempt alone told the user this job had never been read.
  const latestStatus = job.data?.latest_run?.status;
  const requirements = useJobRequirements(jobId, latestStatus === "failed");
  const runs = useTailoredResumesForJob(jobId);
  const start = useStartTailoring(jobId);
  // Asked before Generate is offered rather than discovered from its 409.
  const versions = useConfirmedVersions();
  // Read so the page knows which version the carried match judged — a link
  // with only ?match= does not say — and that it belongs to this job at all.
  const carried = useMatch(fromMatch);

  usePageTitle("Tailor resume", job.data?.job_title);

  // A match judges ONE version. Tailoring a different version from it is
  // refused by the server (409), and before that check existed it silently
  // steered a version 2 resume by a version 1 match — because the version was
  // lost between the two pages and this picker reset to "current". So the
  // match's own version is the default selection here, the request names it
  // explicitly beside the match, and picking any other version drops the match
  // and says so.
  const matchVersionId = carried.data?.profile_version_id ?? linkedVersionId;
  const matchUnusable =
    carried.isError || (carried.data !== undefined && carried.data.job_id !== jobId);
  const isConfirmed = (id: number | undefined) =>
    id !== undefined && versions.confirmed.some((version) => version.id === id);

  const pickerValue: number | undefined = choice
    ? choice.value
    : fromMatch && !matchUnusable && isConfirmed(matchVersionId)
      ? matchVersionId
      : isConfirmed(linkedVersionId)
        ? linkedVersionId
        : undefined;
  const { version: selected, send } = resolveVersion(pickerValue, versions);

  const matchApplies =
    fromMatch !== undefined &&
    !matchUnusable &&
    matchVersionId !== undefined &&
    selected?.id === matchVersionId;
  const matchForOtherVersion =
    fromMatch !== undefined &&
    !matchUnusable &&
    matchVersionId !== undefined &&
    selected !== undefined &&
    selected.id !== matchVersionId;
  const matchVersionNumber =
    carried.data?.profile_version_number ??
    versions.confirmed.find((version) => version.id === matchVersionId)?.version_number;

  const latest = runs.data?.[0];
  const active = useTailoredResume(
    latest && isTailoring(latest) ? latest.id : undefined,
  );

  // The polled run, but only while it is genuinely still running.
  //
  // Two bugs lived in the earlier version of this line, both from taking
  // `active.data` as in-flight unconditionally. When the poll flipped to a
  // completed run the page went on calling it ongoing — rendering "Ready" as
  // though it were a pipeline stage, under "this page updates itself while the
  // work runs" — until the separate list query caught up a poll later. And a
  // STALLED run counted as in-flight forever, which disabled the only Generate
  // button on the page while the alert beside it advised generating again.
  const polled = active.data && isTailoring(active.data) ? active.data : null;

  // The POST's own response, and only for as long as it is the only thing that
  // knows this run exists.
  //
  // `start.data` is a snapshot of the moment the run was queued and is never
  // refetched, so it is the one value on this page guaranteed to go stale. It
  // earns its place for the gap between the POST answering and the list
  // refetch it triggers — without it, pressing Generate shows nothing at all
  // for a beat. Beyond that gap it is a liar: a run that finished before the
  // list caught up leaves `polled` empty, and an unbounded fallback then
  // reports QUEUED forever, under a disabled Generate button, beside a list
  // already showing the finished document. Bounding it to "the list has not
  // heard of this run yet" is what makes it a gap-filler rather than a state.
  const unlisted =
    start.data && !runs.data?.some((run) => run.id === start.data!.id) ? start.data : null;

  const pending = polled ?? (unlisted && isTailoring(unlisted) ? unlisted : null);

  // Stalled is not "working", it is "stopped without saying so". It still gets
  // its own panel, but it must not hold the button hostage.
  const inFlight = pending && !pending.is_stalled ? pending : null;
  const stalled = pending && pending.is_stalled ? pending : null;

  // The run this page watched in flight, so its finishing can be SAID. The
  // in-flight panel simply vanished when the work ended, which a screen-reader
  // user — or anyone who looked away — could not tell apart from nothing having
  // happened. The detail poll is read first because it lands before the list.
  const [watchedId, setWatchedId] = React.useState<number | null>(null);
  const inFlightId = inFlight?.id;
  React.useEffect(() => {
    if (inFlightId !== undefined) setWatchedId(inFlightId);
  }, [inFlightId]);
  const finished =
    watchedId === null || inFlight
      ? undefined
      : active.data?.id === watchedId && !isTailoring(active.data)
        ? active.data
        : runs.data?.find((run) => run.id === watchedId && !isTailoring(run));

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
    return <PageState heading="Tailor resume">{notFound}</PageState>;
  }
  if (job.isPending) {
    return (
      <PageState heading="Tailor resume">
        <LoadingCard label="Loading job" />
      </PageState>
    );
  }
  if (job.isError && job.error instanceof ApiError && job.error.isNotFound) {
    return <PageState heading="Tailor resume">{notFound}</PageState>;
  }
  if (job.isError) {
    return (
      <PageState heading="Tailor resume">
        <ApiErrorAlert error={job.error} onRetry={() => void job.refetch()} />
      </PageState>
    );
  }

  const earlierReading = latestStatus === "failed" && Boolean(requirements.data?.requirements);
  const structured = latestStatus === "completed" || earlierReading;
  const readingPending = latestStatus === "failed" && requirements.isPending;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold">Tailor resume</h1>
          <p className="mt-1 truncate text-sm text-muted-foreground">
            {job.data.job_title}
            {job.data.company_name ? ` · ${job.data.company_name}` : ""}
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link href={`/jobs/${jobId}`}>Back to job</Link>
        </Button>
      </div>

      {readingPending ? (
        <LoadingCard label="Loading requirements" />
      ) : requirements.isError ? (
        <ApiErrorAlert error={requirements.error} onRetry={() => void requirements.refetch()} />
      ) : !structured ? (
        <Alert>
          <AlertTitle>This job has not been read yet</AlertTitle>
          <AlertDescription>
            <p>
              A resume is written against the posting&rsquo;s structured requirements, and
              this job does not have any yet.
            </p>
            <div className="mt-3">
              <Button variant="outline" size="sm" asChild>
                <Link href={`/jobs/${jobId}`}>Go to the job</Link>
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Generate</CardTitle>
            <CardDescription>
              EasyApply selects the parts of a confirmed profile version that answer this
              posting and writes them into a resume. Every statement is then checked back
              against that version — anything it cannot trace is reported and the file is
              withheld.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {earlierReading ? (
              <p className="text-xs text-muted-foreground">
                The latest attempt to read this posting failed, so the resume is written against
                the earlier reading shown on the job page.
              </p>
            ) : null}

            {/* A list that could not be read is not a profile with nothing
                confirmed in it, and must not be reported as one. */}
            {versions.isError ? (
              <ApiErrorAlert error={versions.error} onRetry={versions.refetch} />
            ) : null}

            {!versions.isPending && !versions.isError && !versions.hasConfirmedVersion ? (
              <NoConfirmedVersion action="tailored" />
            ) : null}

            <VersionPicker
              value={pickerValue}
              onChange={(value) => setChoice({ value })}
              label="Build from"
            />

            {matchApplies ? (
              <p className="text-xs text-muted-foreground">
                Using your match for this job
                {matchVersionNumber ? ` (version ${matchVersionNumber})` : ""} to decide what to
                lead with. A requirement the match could not evidence stays unevidenced — the
                match never lets the resume claim more than the version holds.
              </p>
            ) : null}

            {matchForOtherVersion && selected ? (
              <p className="text-xs text-muted-foreground">
                Your match for this job judged
                {matchVersionNumber ? ` version ${matchVersionNumber}` : " another version"}, so it
                is not used for version {selected.version_number} — a match only speaks for the
                version it judged.{" "}
                <Link
                  className="underline underline-offset-2"
                  href={`/jobs/${jobId}/match?version=${selected.id}`}
                >
                  Match version {selected.version_number}
                </Link>{" "}
                first if you want it to decide the order.
              </p>
            ) : null}

            {fromMatch && matchUnusable ? (
              <p className="text-xs text-muted-foreground">
                The match this page was opened from could not be used, so the resume will be
                ordered without one.
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <Button
                onClick={() =>
                  start.mutate(
                    // With a match, its own version is named beside it: the
                    // server refuses a match for any other version. Without
                    // one, `send` omits the id only for "current".
                    matchApplies
                      ? { profile_version_id: matchVersionId, match_result_id: fromMatch }
                      : send !== undefined
                        ? { profile_version_id: send }
                        : {},
                    {
                      onSuccess: (run) => {
                        // A run that finished before the response came back —
                        // an inline queue, a very small profile — has nothing
                        // to watch, so go straight to it.
                        if (!isTailoring(run)) router.push(`/tailored-resumes/${run.id}`);
                      },
                    },
                  )
                }
                disabled={
                  start.isPending ||
                  Boolean(inFlight) ||
                  !versions.hasConfirmedVersion ||
                  // Until the carried match says which version it judged, a
                  // click would have to guess whether it applies.
                  (fromMatch !== undefined && carried.isPending)
                }
              >
                {start.isPending
                  ? "Starting…"
                  : inFlight
                    ? "Generating…"
                    : "Generate resume"}
              </Button>

              {runs.data && runs.data.length > 0 ? (
                <Button variant="outline" asChild>
                  <Link href={`/tailored-resumes/${runs.data[0].id}`}>
                    Open the last one
                  </Link>
                </Button>
              ) : null}
            </div>

            {start.isError ? (
              <TailoringRefusal error={start.error} jobId={jobId} versionId={selected?.id} />
            ) : null}
          </CardContent>
        </Card>
      )}

      {stalled ? <Stalled /> : null}
      {inFlight ? <InFlight run={inFlight} /> : null}

      {/* Always in the document, so assistive technology is already watching
          it when the sentence arrives; a live region inserted together with its
          text is often not announced at all. Polite, and not a `status` role:
          the stage line above is the page's status while the work runs. */}
      <div aria-live="polite">
        {finished ? (
          <Card>
            <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
              <p className="text-sm">{finishedSentence(finished)}</p>
              <Button asChild>
                <Link href={`/tailored-resumes/${finished.id}`}>Review the tailored resume</Link>
              </Button>
            </CardContent>
          </Card>
        ) : null}
      </div>

      {runs.isError ? (
        <ApiErrorAlert error={runs.error} onRetry={() => void runs.refetch()} />
      ) : null}
      {runs.isPending ? <LoadingCard label="Loading tailored resumes" /> : null}

      {runs.isSuccess && runs.data.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Previous attempts</CardTitle>
            <CardDescription>
              Every generation is kept, so a document you sent can still be explained.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {runs.data.map((run) => (
                <li key={run.id}>
                  <Link
                    href={`/tailored-resumes/${run.id}`}
                    className="flex items-center justify-between gap-3 py-3 hover:opacity-80"
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-medium">
                        Attempt {run.attempt}
                        {run.profile_version_number
                          ? ` · from version ${run.profile_version_number}`
                          : ""}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {formatDateTime(run.created_at)}
                      </span>
                    </span>
                    <TailoringStatusBadge run={run} />
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {runs.isSuccess && runs.data.length === 0 && !inFlight && structured ? (
        <EmptyState
          title="Nothing generated for this job yet"
          description="Generate a resume and you will be able to review every line against your profile before downloading it."
        />
      ) : null}
    </div>
  );
}

/** What the watched run came to, in one sentence. */
function finishedSentence(run: { status: string; grounding_status?: string | null }): string {
  if (run.status === "failed") return "The generation you started has failed.";
  if (run.grounding_status === "passed") return "Your tailored resume is ready.";
  return "The generation finished, but the resume was not approved, so no file was produced.";
}

/**
 * A run that stopped without saying so.
 *
 * Its own component rather than a branch inside `InFlight`, because the caller
 * has to treat it differently: a stalled run must not disable the Generate
 * button. It used to, which made this alert's advice impossible to follow —
 * "generating again is safe" beside a disabled button is a dead end, and §11
 * forbids exactly this shape of screen.
 */
function Stalled() {
  return (
    <Alert variant="destructive">
      <AlertTitle>This generation stopped responding</AlertTitle>
      <AlertDescription>
        <p>
          It has been running longer than expected and has not finished. Nothing has been
          saved, and nothing was sent anywhere. Use Generate resume above to try again.
        </p>
      </AlertDescription>
    </Alert>
  );
}

/**
 * The wait, told honestly.
 *
 * Each stage is a state the server is genuinely in, so the label changes as the
 * work moves rather than a single spinner covering all of it. It is only ever
 * rendered for a run that is actually still running — see the `pending` /
 * `inFlight` split above for the two ways that used to go wrong.
 */
function InFlight({ run }: { run: { id: number; status: string; attempt: number } }) {
  return (
    <Card>
      <CardContent className="p-6">
        <p role="status" className="text-sm">
          {tailoringStatusLabel(run.status)}…
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {run.status === "validating"
            ? "Checking every generated line against the profile version it came from."
            : run.status === "rendering"
              ? "Writing the document file."
              : "This page updates itself while the work runs. You can leave it; generation continues without it."}
        </p>
        {run.attempt > 1 ? (
          <p className="mt-2 text-xs text-muted-foreground">Attempt {run.attempt}.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * The refusals a user can do something about.
 *
 * Both are 409s carrying the server's own sentence, which is what is shown;
 * only the suggested next step differs, and that is a navigation decision
 * rather than a judgment about the data. The same shape the match page uses.
 */
function TailoringRefusal({
  error,
  jobId,
  versionId,
}: {
  error: unknown;
  jobId: number;
  versionId: number | undefined;
}) {
  if (!(error instanceof ApiError) || !error.isConflict) {
    return <ApiErrorAlert error={error} />;
  }

  // A match made against another version, or against an earlier reading of
  // the job: the way forward is a fresh match of the version being tailored.
  if (/\bmatch\b/i.test(error.message)) {
    return (
      <Alert>
        <AlertTitle>This resume cannot be generated from that match</AlertTitle>
        <AlertDescription>
          <p>{error.message}</p>
          <div className="mt-3">
            <Button variant="outline" size="sm" asChild>
              <Link href={`/jobs/${jobId}/match${versionId ? `?version=${versionId}` : ""}`}>
                Go to match
              </Link>
            </Button>
          </div>
        </AlertDescription>
      </Alert>
    );
  }

  const aboutProfile = /confirm|draft/i.test(error.message);

  return (
    <Alert>
      <AlertTitle>This resume cannot be generated yet</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <div className="mt-3">
          <Button variant="outline" size="sm" asChild>
            <Link href={aboutProfile ? "/profile" : `/jobs/${jobId}`}>
              {aboutProfile ? "Go to profile" : "Go to job"}
            </Link>
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
