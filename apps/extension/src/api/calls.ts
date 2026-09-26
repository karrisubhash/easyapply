/**
 * Every request the popup may ask the service worker to make, as data.
 *
 * A closed list, not a general "fetch this path" message: the worker accepts
 * one of these shapes, with positive integer ids and nothing else, and turns it
 * into exactly one request whose path it builds itself. So nothing the popup
 * renders — a job description, a requirement, a page's text — can reach a URL,
 * and no call can name a route the extension has no business with.
 *
 * Reads are all GETs. The two writes are the ones the extension's workflow
 * needs and the web app's proxy admits from it (apps/web/src/server/
 * request-guard.ts): create-or-return a match, and start a tailoring run. Both
 * are idempotent server-side — a repeated match answers with the stored one; a
 * second start while a run is in flight answers with that run.
 *
 * No call carries identity. The bodies are the API's own request schemas
 * (`MatchRequest`, `TailoringRequest`, both `extra="forbid"`), holding ids of
 * things the web app already showed this person — ids the API resolves inside
 * that person's own account, so a foreign one is a 404, never a peek.
 */

import type {
  JobRequirementsResult,
  Match,
  MatchDetail,
  MatchRequest,
  ProfileVersion,
  ProfileVersionSummary,
  TailoringRequest,
  TailoringRun,
  TailoringRunDetail,
} from "@easyapply/shared";

import type { ApiRequest } from "./client";
import {
  isMatchDetail,
  isMatchList,
  isProfileVersion,
  isProfileVersionList,
  isRequirementsResult,
  isTailoringRunDetail,
  isTailoringRunList,
} from "./guards";

export type ApiCall =
  | { op: "requirements"; jobId: number }
  | { op: "profileVersions" }
  | { op: "currentProfileVersion" }
  | { op: "matches"; jobId: number }
  | { op: "match"; matchId: number }
  | { op: "createMatch"; jobId: number; profileVersionId: number }
  | { op: "tailoringRuns"; jobId: number }
  | { op: "tailoringRun"; runId: number }
  | { op: "startTailoring"; jobId: number; profileVersionId: number; matchResultId: number | null };

export type ApiOp = ApiCall["op"];

export interface CallResults {
  requirements: JobRequirementsResult;
  profileVersions: ProfileVersionSummary[];
  currentProfileVersion: ProfileVersion;
  matches: Match[];
  match: MatchDetail;
  createMatch: MatchDetail;
  tailoringRuns: TailoringRun[];
  tailoringRun: TailoringRunDetail;
  startTailoring: TailoringRunDetail;
}

export type ResultOf<C extends ApiCall> = CallResults[C["op"]];

/** A database id as the API issues them: a positive safe integer. */
export function isId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

/** Which keys each call has besides `op`, and whether each may be null. */
const SHAPES: Record<ApiOp, Record<string, "id" | "id?">> = {
  requirements: { jobId: "id" },
  profileVersions: {},
  currentProfileVersion: {},
  matches: { jobId: "id" },
  match: { matchId: "id" },
  createMatch: { jobId: "id", profileVersionId: "id" },
  tailoringRuns: { jobId: "id" },
  tailoringRun: { runId: "id" },
  startTailoring: { jobId: "id", profileVersionId: "id", matchResultId: "id?" },
};

/** Whether a message is exactly one of the calls above — no unknown op, no extra key, no bad id. */
export function isApiCall(value: unknown): value is ApiCall {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const call = value as Record<string, unknown>;
  const shape = typeof call.op === "string" && Object.hasOwn(SHAPES, call.op) ? SHAPES[call.op as ApiOp] : null;
  if (!shape) return false;
  const keys = Object.keys(call).filter((key) => key !== "op");
  if (keys.length !== Object.keys(shape).length || !keys.every((key) => Object.hasOwn(shape, key))) return false;
  return Object.entries(shape).every(([key, kind]) => (kind === "id?" && call[key] === null) || isId(call[key]));
}

export interface PreparedCall<T> extends ApiRequest<T> {
  /** The route with ids replaced, for diagnostics: `GET /jobs/:id/requirements`. */
  route: string;
  /** The ids involved, for diagnostics. Never personal data. */
  ids: Record<string, number>;
  /** Whether it changes anything server-side. */
  write: boolean;
}

function segment(id: number): string {
  if (!isId(id)) throw new RangeError(`not an id: ${String(id)}`);
  return String(id);
}

function get<T>(path: string, route: string, ids: Record<string, number>, accept: (value: unknown) => value is T): PreparedCall<T> {
  return { method: "GET", path, route: `GET ${route}`, ids, accept, write: false };
}

function post<T>(
  path: string,
  route: string,
  ids: Record<string, number>,
  body: MatchRequest | TailoringRequest,
  accept: (value: unknown) => value is T,
): PreparedCall<T> {
  return { method: "POST", path, route: `POST ${route}`, ids, body, accept, write: true };
}

/** The one request a call is. Throws only for a call `isApiCall` would have refused. */
export function prepare<C extends ApiCall>(call: C): PreparedCall<ResultOf<C>> {
  const prepared = ((): PreparedCall<unknown> => {
    switch (call.op) {
      case "requirements":
        return get(`/jobs/${segment(call.jobId)}/requirements`, "/jobs/:id/requirements", { jobId: call.jobId }, isRequirementsResult);
      case "profileVersions":
        return get("/profile/versions", "/profile/versions", {}, isProfileVersionList);
      case "currentProfileVersion":
        return get("/profile/versions/current", "/profile/versions/current", {}, isProfileVersion);
      case "matches":
        return get(`/jobs/${segment(call.jobId)}/matches`, "/jobs/:id/matches", { jobId: call.jobId }, isMatchList);
      case "match":
        return get(`/matches/${segment(call.matchId)}`, "/matches/:id", { matchId: call.matchId }, isMatchDetail);
      case "createMatch":
        return post(
          `/jobs/${segment(call.jobId)}/matches`,
          "/jobs/:id/matches",
          { jobId: call.jobId, profileVersionId: call.profileVersionId },
          { profile_version_id: segmentNumber(call.profileVersionId) },
          isMatchDetail,
        );
      case "tailoringRuns":
        return get(`/jobs/${segment(call.jobId)}/tailored-resumes`, "/jobs/:id/tailored-resumes", { jobId: call.jobId }, isTailoringRunList);
      case "tailoringRun":
        return get(`/tailored-resumes/${segment(call.runId)}`, "/tailored-resumes/:id", { runId: call.runId }, isTailoringRunDetail);
      case "startTailoring":
        return post(
          `/jobs/${segment(call.jobId)}/tailored-resumes`,
          "/jobs/:id/tailored-resumes",
          {
            jobId: call.jobId,
            profileVersionId: call.profileVersionId,
            ...(call.matchResultId === null ? {} : { matchResultId: call.matchResultId }),
          },
          {
            profile_version_id: segmentNumber(call.profileVersionId),
            match_result_id: call.matchResultId === null ? null : segmentNumber(call.matchResultId),
          },
          isTailoringRunDetail,
        );
    }
  })();
  return prepared as PreparedCall<ResultOf<C>>;
}

function segmentNumber(id: number): number {
  segment(id);
  return id;
}

/**
 * A key that is equal for two calls exactly when they are the same request FOR
 * THE SAME ACCOUNT. The account's tag is part of it: `profileVersions` has no
 * id, so without the tag a popup opened for one account while another's call
 * was in flight would be handed the other account's answer. Null — an account
 * the web app gave no tag for — is a key no other call shares: an unknown
 * account is never taken to be the same as anyone.
 */
export function callKey(call: ApiCall, sessionTag: string | null): string | null {
  if (sessionTag === null) return null;
  const { op, ...rest } = call as { op: string } & Record<string, unknown>;
  // A tag is [A-Za-z0-9_-]+ (./session.ts), so the space cannot be part of it.
  return `${sessionTag} ${op}:${Object.keys(rest)
    .sort()
    .map((key) => `${key}=${String(rest[key])}`)
    .join("&")}`;
}
