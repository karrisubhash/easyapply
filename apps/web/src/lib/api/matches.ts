/**
 * The Match API, one function per endpoint.
 *
 * `createMatch` is a POST that is safe to repeat: the server answers 201 for a
 * new evaluation and 200 with the existing one for a repeat, so the UI can
 * offer a "Match" button without owning any of the logic that decides whether
 * a match already exists. `profile_version_id` is optional and omitted by
 * default — the server then uses the caller's current CONFIRMED version, which
 * is well-defined and immutable, and a version belonging to someone else is a
 * 404 resolved server-side. The frontend never picks a version on the user's
 * behalf from a list it happens to be holding.
 */

import type { Match, MatchDetail } from "@easyapply/shared";

import { request } from "./client";

export function listMatches(jobId: number): Promise<Match[]> {
  return request<Match[]>(`jobs/${jobId}/matches`);
}

export function getMatch(matchId: number): Promise<MatchDetail> {
  return request<MatchDetail>(`matches/${matchId}`);
}

export function createMatch(
  jobId: number,
  profileVersionId?: number,
): Promise<MatchDetail> {
  return request<MatchDetail>(`jobs/${jobId}/matches`, {
    method: "POST",
    body: JSON.stringify(
      profileVersionId === undefined ? {} : { profile_version_id: profileVersionId },
    ),
  });
}
