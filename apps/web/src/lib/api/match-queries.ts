"use client";

import {
  MATCH_CATEGORY_LABELS,
  MATCH_CATEGORY_ORDER,
  type MatchCategory,
  type MatchDetail,
  type MatchRequirementResult,
} from "@easyapply/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import * as api from "./matches";

export const matchKeys = {
  forJob: (jobId: number) => ["jobs", jobId, "matches"] as const,
  detail: (id: number) => ["matches", id] as const,
};

function retryUnlessExpected(failureCount: number, error: unknown): boolean {
  // A 409 means the job is not structured yet or the profile has no confirmed
  // version. Both are states a retry cannot change, and both are things to
  // tell the user rather than to keep asking the server about.
  if (error instanceof ApiError && (error.isSessionFailure || error.isNotFound || error.isConflict)) {
    return false;
  }
  return failureCount < 2;
}

export function useMatches(jobId: number) {
  return useQuery({
    queryKey: matchKeys.forJob(jobId),
    queryFn: () => api.listMatches(jobId),
    retry: retryUnlessExpected,
    enabled: Number.isInteger(jobId) && jobId > 0,
  });
}

export function useMatch(matchId: number | undefined) {
  return useQuery({
    queryKey: matchKeys.detail(matchId ?? 0),
    queryFn: () => api.getMatch(matchId as number),
    retry: retryUnlessExpected,
    enabled: Number.isInteger(matchId) && (matchId ?? 0) > 0,
  });
}

export function useCreateMatch(jobId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (profileVersionId?: number) => api.createMatch(jobId, profileVersionId),
    onSuccess: (match) => {
      // Seed the detail cache from the response so opening the result does not
      // re-fetch what the POST already returned in full.
      client.setQueryData(matchKeys.detail(match.id), match);
      client.invalidateQueries({ queryKey: matchKeys.forJob(jobId) });
    },
  });
}

// --- reading a result --------------------------------------------------------
//
// Grouping and counting live here rather than in the page so they can be
// tested without rendering, and so every view of a match agrees about what
// "required, missing" means. None of these functions decide anything the
// server did not already decide — they arrange verdicts, never derive them.

export interface CategoryGroup {
  category: MatchCategory;
  required: MatchRequirementResult[];
  preferred: MatchRequirementResult[];
}

/** The order categories are shown in: what a person checks first, first.
 * Shared with the Chrome extension (@easyapply/shared, presentation.ts). */
export const CATEGORY_ORDER: readonly MatchCategory[] = MATCH_CATEGORY_ORDER;

export const CATEGORY_LABELS: Record<MatchCategory, string> = MATCH_CATEGORY_LABELS;

export function groupByCategory(results: MatchRequirementResult[]): CategoryGroup[] {
  return CATEGORY_ORDER.map((category) => {
    const inCategory = results.filter((result) => result.category === category);
    return {
      category,
      required: inCategory.filter((result) => result.is_required),
      preferred: inCategory.filter((result) => !result.is_required),
    };
  }).filter((group) => group.required.length > 0 || group.preferred.length > 0);
}

/**
 * The counts, read off the stored columns rather than recomputed.
 *
 * The server stores them precisely so they are not re-derived, and a UI that
 * counted the rows itself could disagree with the number it is displaying
 * beside them.
 */
export function axisCounts(match: MatchDetail | undefined) {
  return {
    required: {
      total: match?.required_total ?? 0,
      matched: match?.required_matched ?? 0,
      missing: match?.required_missing ?? 0,
      uncertain: match?.required_uncertain ?? 0,
    },
    preferred: {
      total: match?.preferred_total ?? 0,
      matched: match?.preferred_matched ?? 0,
      missing: match?.preferred_missing ?? 0,
      uncertain: match?.preferred_uncertain ?? 0,
    },
  };
}

/**
 * A coverage fraction as a whole-number percentage.
 *
 * Named for what it measures. This is coverage of structured job requirements
 * by available profile evidence — not a probability of an interview, an offer,
 * or a hire — and no caller may label it as one. See
 * services/api/app/services/matching/scoring.py.
 */
export function asPercentage(coverage: number): number {
  return Math.round(coverage * 100);
}
