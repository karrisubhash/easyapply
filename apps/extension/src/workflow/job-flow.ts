/**
 * What the popup knows about one saved job, as an explicit state machine.
 *
 *   requirements  loading ─▶ processing ─▶ ready | failed | unavailable | error
 *                                 └──────▶ still_processing (budget spent; "Check again")
 *   profile       idle ─▶ loading ─▶ current | no_profile | no_versions | drafts_only | no_current | error
 *   match         idle ─▶ loading ─▶ none ─▶ matching ─▶ matched | error
 *                               └─▶ matched
 *   tailoring     idle ─▶ loading ─▶ none ─▶ starting ─▶ running ─▶ ready | not_approved | no_file | failed
 *                               └─▶ (any of the above)          └▶ still_running
 *   session       active ─▶ expired   (any answer that was the proxy's 401)
 *                        └─▶ changed   (the proxy's 409 session_changed: the browser
 *                                       is signed in to another account now; every
 *                                       state above is discarded, and nothing
 *                                       arriving afterwards is read)
 *
 * Every state here is something the BACKEND said, classified — never a
 * conclusion of the extension's own. A requirement's text, a verdict, a count,
 * a grounding status: each is carried as the API sent it, for the popup to
 * render. Nothing is computed from them.
 *
 * Staleness is structural. Every answer is dispatched with the key it was asked
 * under — which account (by its tag, never its label), which tab, which job —
 * and match and tailoring answers also with the profile version they were
 * for. An answer whose key is not the flow's current one is dropped unread. So a slow response cannot write one
 * person's match into another person's popup, and a match for profile version 2
 * cannot be shown as the match for version 3: when the current version changes,
 * the match and tailoring states are thrown away and re-read for the new one.
 */

import type {
  JobProcessingRun,
  JobRequirements,
  JobRequirementsResult,
  MatchDetail,
  ProfileVersionSummary,
  TailoringRunDetail,
} from "@easyapply/shared";
import { JOB_ACTIVE_PROCESSING_STATUSES, TAILORING_ACTIVE_STATUSES } from "@easyapply/shared";

import type { ApiFailure, ApiResult } from "../api/client";

/** Whose view this is. Answers asked under a different key are dropped. */
export interface FlowKey {
  /**
   * The tag of the account the web app named when the popup opened
   * (src/api/session.ts): what "the same account" means here. Null is an
   * unknown account, which is the same as no one — not even itself.
   */
  sessionTag: string | null;
  /** What to call that account on screen. Display only: two accounts can share it. */
  label: string;
  tabId: number;
  jobId: number;
}

export type ReadyRequirements = JobRequirementsResult & { requirements: JobRequirements };

export type RequirementsState =
  | { kind: "loading" }
  /** Queued or being read. `run` is the attempt in progress. */
  | { kind: "processing"; run: JobProcessingRun | null; checks: number }
  /** Still in progress when the poll budget ran out. Not a failure, not a completion. */
  | { kind: "still_processing"; run: JobProcessingRun | null }
  /** The only attempts failed; nothing was stored. */
  | { kind: "failed"; run: JobProcessingRun }
  /** No run at all, or a finished one that stored nothing. */
  | { kind: "unavailable" }
  | { kind: "ready"; result: ReadyRequirements }
  | { kind: "error"; failure: ApiFailure };

export type ProfileState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "no_profile" }
  | { kind: "no_versions" }
  | { kind: "drafts_only"; drafts: number }
  /** Confirmed versions exist but none is current, so there is no default to use. */
  | { kind: "no_current"; confirmed: number }
  | { kind: "current"; version: ProfileVersionSummary }
  | { kind: "error"; failure: ApiFailure };

export type MatchState =
  | { kind: "idle" }
  | { kind: "loading" }
  /** No stored match for the current profile version. */
  | { kind: "none" }
  | { kind: "matching" }
  /** `created`: true for a new evaluation (201), false for the stored one returned (200), null when read from the list. */
  | { kind: "matched"; match: MatchDetail; created: boolean | null }
  | { kind: "error"; failure: ApiFailure; during: "load" | "match" };

export type TailoringState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "starting" }
  | { kind: "running"; run: TailoringRunDetail; checks: number }
  | { kind: "still_running"; run: TailoringRunDetail }
  /** Completed, grounding passed, and a file exists. */
  | { kind: "ready"; run: TailoringRunDetail }
  /** Completed, but grounding failed: the document is withheld and has no file. */
  | { kind: "not_approved"; run: TailoringRunDetail }
  /** Completed and passed, yet no file is recorded — the artifact is unavailable. */
  | { kind: "no_file"; run: TailoringRunDetail }
  | { kind: "failed"; run: TailoringRunDetail }
  | { kind: "error"; failure: ApiFailure; during: "load" | "start" };

export interface JobFlow {
  key: FlowKey;
  session: "active" | "expired" | "changed";
  requirements: RequirementsState;
  profile: ProfileState;
  /** The profile version `match` and `tailoring` belong to; null until one is current. */
  versionId: number | null;
  match: MatchState;
  tailoring: TailoringState;
}

export type FlowEvent =
  | { type: "requirements"; key: FlowKey; result: ApiResult<JobRequirementsResult>; checks: number; final: boolean }
  | { type: "requirements_recheck"; key: FlowKey }
  | { type: "profile_loading"; key: FlowKey }
  | { type: "profile"; key: FlowKey; result: ApiResult<ProfileVersionSummary[]> }
  | { type: "match_loading"; key: FlowKey; versionId: number }
  /** The newest stored match for this version, or null when there is none. */
  | { type: "match_loaded"; key: FlowKey; versionId: number; result: ApiResult<MatchDetail | null> }
  | { type: "match_requested"; key: FlowKey; versionId: number }
  | { type: "match_created"; key: FlowKey; versionId: number; result: ApiResult<MatchDetail> }
  | { type: "tailoring_loading"; key: FlowKey; versionId: number }
  /** The newest run for this version (null: none), and whether polling has given up on it. */
  | {
      type: "tailoring_loaded";
      key: FlowKey;
      versionId: number;
      result: ApiResult<TailoringRunDetail | null>;
      checks: number;
      final: boolean;
    }
  | { type: "tailoring_requested"; key: FlowKey; versionId: number }
  | { type: "tailoring_started"; key: FlowKey; versionId: number; result: ApiResult<TailoringRunDetail> };

export function sameKey(a: FlowKey, b: FlowKey): boolean {
  return a.sessionTag !== null && a.sessionTag === b.sessionTag && a.tabId === b.tabId && a.jobId === b.jobId;
}

export function initialFlow(key: FlowKey): JobFlow {
  return {
    key,
    session: "active",
    requirements: { kind: "loading" },
    profile: { kind: "idle" },
    versionId: null,
    match: { kind: "idle" },
    tailoring: { kind: "idle" },
  };
}

const ACTIVE_JOB = new Set<string>(JOB_ACTIVE_PROCESSING_STATUSES);
const ACTIVE_TAILORING = new Set<string>(TAILORING_ACTIVE_STATUSES);

export function isActiveJobRun(run: JobProcessingRun | null | undefined): boolean {
  return !!run && ACTIVE_JOB.has(run.status);
}

export function isActiveTailoringRun(run: { status: string } | null | undefined): boolean {
  return !!run && ACTIVE_TAILORING.has(run.status);
}

/** Whether a requirements answer is final — nothing more will change by waiting. */
export function requirementsSettled(result: ApiResult<JobRequirementsResult>): boolean {
  if (!result.ok) return true;
  if (result.data.requirements) return true;
  return !isActiveJobRun(result.data.job.latest_run ?? result.data.run);
}

/** Whether a tailoring answer is final. */
export function tailoringSettled(result: ApiResult<TailoringRunDetail | null>): boolean {
  return !result.ok || result.data === null || !isActiveTailoringRun(result.data);
}

function classifyRequirements(
  result: ApiResult<JobRequirementsResult>,
  checks: number,
  final: boolean,
): RequirementsState {
  if (!result.ok) return { kind: "error", failure: result };
  const { data } = result;
  if (data.requirements) return { kind: "ready", result: data as ReadyRequirements };
  // No reading stored. What is the newest attempt doing?
  const run = data.job.latest_run ?? data.run ?? null;
  if (isActiveJobRun(run)) return final ? { kind: "still_processing", run } : { kind: "processing", run, checks };
  if (run && run.status === "failed") return { kind: "failed", run };
  return { kind: "unavailable" };
}

function classifyProfile(result: ApiResult<ProfileVersionSummary[]>): ProfileState {
  if (!result.ok) {
    // GET /profile/versions answers 404 for exactly one reason: no profile.
    return result.kind === "not_found" ? { kind: "no_profile" } : { kind: "error", failure: result };
  }
  const versions = result.data;
  if (versions.length === 0) return { kind: "no_versions" };
  // `is_current` is only ever set on a confirmed version (the database forbids
  // it on a draft); both are checked anyway, so a draft is never matched.
  const current = versions.find((version) => version.is_current && version.status === "confirmed");
  if (current) return { kind: "current", version: current };
  const confirmed = versions.filter((version) => version.status === "confirmed").length;
  return confirmed > 0 ? { kind: "no_current", confirmed } : { kind: "drafts_only", drafts: versions.length };
}

/** A match belongs on screen only if it is for this job and this profile version. */
function belongs(flow: JobFlow, versionId: number, item: { job_id: number; profile_version_id: number }): boolean {
  return item.job_id === flow.key.jobId && item.profile_version_id === versionId;
}

export function classifyTailoringRun(run: TailoringRunDetail, checks: number, final: boolean): TailoringState {
  if (isActiveTailoringRun(run)) return final ? { kind: "still_running", run } : { kind: "running", run, checks };
  if (run.status === "completed") {
    if (run.grounding_status !== "passed") return { kind: "not_approved", run };
    return run.has_artifact ? { kind: "ready", run } : { kind: "no_file", run };
  }
  return { kind: "failed", run };
}

/**
 * What an answer says about the session, applied after the answer itself:
 * the proxy's 401 ends it (the states stay, hidden behind the notice); its 409
 * `session_changed` means the view belongs to an account this browser is no
 * longer signed in to, so every state of that account is thrown away.
 */
function endIfSessionGone(flow: JobFlow, result: ApiResult<unknown>): JobFlow {
  if (result.ok) return flow;
  if (result.kind === "session_changed") return { ...initialFlow(flow.key), session: "changed" };
  return result.kind === "session_required" ? { ...flow, session: "expired" } : flow;
}

export function reduce(flow: JobFlow, event: FlowEvent): JobFlow {
  // An answer for a view that is no longer on screen is not read at all.
  if (!sameKey(flow.key, event.key)) return flow;
  // Nor is anything for an account the browser has left.
  if (flow.session === "changed") return flow;
  if ("versionId" in event && event.versionId !== flow.versionId) return flow;

  switch (event.type) {
    case "requirements":
      return endIfSessionGone(
        { ...flow, requirements: classifyRequirements(event.result, event.checks, event.final) },
        event.result,
      );

    case "requirements_recheck":
      return { ...flow, requirements: { kind: "loading" } };

    case "profile_loading":
      return { ...flow, profile: { kind: "loading" } };

    case "profile": {
      const profile = classifyProfile(event.result);
      const versionId = profile.kind === "current" ? profile.version.id : null;
      // A different current version (or none) means every match and tailoring
      // state on screen belonged to something else. Drop them; they are re-read.
      const moved = versionId !== flow.versionId;
      return endIfSessionGone(
        {
          ...flow,
          profile,
          versionId,
          match: moved ? { kind: "idle" } : flow.match,
          tailoring: moved ? { kind: "idle" } : flow.tailoring,
        },
        event.result,
      );
    }

    case "match_loading":
      return { ...flow, match: { kind: "loading" } };

    case "match_loaded": {
      const { result } = event;
      if (!result.ok) return endIfSessionGone({ ...flow, match: { kind: "error", failure: result, during: "load" } }, result);
      const match = result.data;
      return {
        ...flow,
        match: match && belongs(flow, event.versionId, match) ? { kind: "matched", match, created: null } : { kind: "none" },
      };
    }

    case "match_requested":
      return { ...flow, match: { kind: "matching" } };

    case "match_created": {
      const { result } = event;
      if (!result.ok) return endIfSessionGone({ ...flow, match: { kind: "error", failure: result, during: "match" } }, result);
      if (!belongs(flow, event.versionId, result.data)) {
        // The API answered with a match for something else. Never show it as this one.
        return {
          ...flow,
          match: {
            kind: "error",
            during: "match",
            failure: {
              ok: false,
              kind: "unexpected_response",
              status: result.status,
              code: null,
              message: "EasyApply answered with a match for a different job or profile version.",
            },
          },
        };
      }
      return { ...flow, match: { kind: "matched", match: result.data, created: result.status === 201 } };
    }

    case "tailoring_loading":
      return { ...flow, tailoring: { kind: "loading" } };

    case "tailoring_loaded": {
      const { result } = event;
      if (!result.ok) {
        return endIfSessionGone({ ...flow, tailoring: { kind: "error", failure: result, during: "load" } }, result);
      }
      const run = result.data;
      return {
        ...flow,
        tailoring:
          run && belongs(flow, event.versionId, run)
            ? classifyTailoringRun(run, event.checks, event.final)
            : { kind: "none" },
      };
    }

    case "tailoring_requested":
      return { ...flow, tailoring: { kind: "starting" } };

    case "tailoring_started": {
      const { result } = event;
      if (!result.ok) {
        return endIfSessionGone({ ...flow, tailoring: { kind: "error", failure: result, during: "start" } }, result);
      }
      if (!belongs(flow, event.versionId, result.data)) {
        return {
          ...flow,
          tailoring: {
            kind: "error",
            during: "start",
            failure: {
              ok: false,
              kind: "unexpected_response",
              status: result.status,
              code: null,
              message: "EasyApply answered with a resume for a different job or profile version.",
            },
          },
        };
      }
      return { ...flow, tailoring: classifyTailoringRun(result.data, 1, false) };
    }
  }
}

// --- what may be offered ---------------------------------------------------------

/** The version a match or tailoring request would be for, when one may be made at all. */
export function currentVersion(flow: JobFlow): ProfileVersionSummary | null {
  return flow.profile.kind === "current" ? flow.profile.version : null;
}

export function canMatch(flow: JobFlow): boolean {
  return (
    flow.session === "active" &&
    flow.requirements.kind === "ready" &&
    currentVersion(flow) !== null &&
    (flow.match.kind === "none" || flow.match.kind === "error")
  );
}

export function canTailor(flow: JobFlow): boolean {
  if (flow.session !== "active" || flow.requirements.kind !== "ready" || currentVersion(flow) === null) return false;
  switch (flow.tailoring.kind) {
    case "none":
    case "not_approved":
    case "no_file":
    case "error":
      return true;
    case "failed":
      return flow.tailoring.run.can_retry !== false;
    default:
      return false;
  }
}

/** The match a tailoring run should be informed by: the one on screen, if it is this version's. */
export function matchForTailoring(flow: JobFlow): number | null {
  return flow.match.kind === "matched" && flow.match.match.profile_version_id === flow.versionId ? flow.match.match.id : null;
}
