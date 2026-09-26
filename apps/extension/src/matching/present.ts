/**
 * A stored match, arranged for the popup.
 *
 * The backend is the sole source of truth for a match: every verdict, count,
 * coverage figure, explanation and piece of evidence is shown as the API sent
 * it. This module only orders and groups — by category, required before
 * preferred, in the order the web app uses — and turns the API's words into the
 * shared labels (@easyapply/shared presentation.ts). It computes no score,
 * re-derives no count, and never changes a verdict.
 *
 * A result whose category or status this build does not know is still shown,
 * under the backend's own word for it: hiding a verdict would be a second
 * opinion, and the wrong one.
 */

import {
  MATCH_CATEGORY_LABELS,
  MATCH_CATEGORY_ORDER,
  MATCH_EVIDENCE_TIER_LABELS,
  MATCH_STATUS_LABELS,
  type MatchCategory,
  type MatchEvidenceTier,
  type MatchRequirementResult,
  type MatchStatus,
} from "@easyapply/shared";

export interface MatchGroup {
  category: string;
  label: string;
  required: MatchRequirementResult[];
  preferred: MatchRequirementResult[];
}

export function matchGroups(results: readonly MatchRequirementResult[]): MatchGroup[] {
  const known = new Set<string>(MATCH_CATEGORY_ORDER);
  const categories = [
    ...MATCH_CATEGORY_ORDER,
    // Anything the backend sent that this build has no label for, in the order it came.
    ...results.map((result) => result.category).filter((category, index, all) => !known.has(category) && all.indexOf(category) === index),
  ];
  return categories
    .map((category) => {
      const inCategory = results.filter((result) => result.category === category);
      return {
        category,
        label: MATCH_CATEGORY_LABELS[category as MatchCategory] ?? category,
        required: inCategory.filter((result) => result.is_required),
        preferred: inCategory.filter((result) => !result.is_required),
      };
    })
    .filter((group) => group.required.length > 0 || group.preferred.length > 0);
}

export function statusLabel(status: string): string {
  return MATCH_STATUS_LABELS[status as MatchStatus] ?? status;
}

export function tierLabel(tier: string | null | undefined): string | null {
  if (!tier) return null;
  return MATCH_EVIDENCE_TIER_LABELS[tier as MatchEvidenceTier] ?? tier;
}

/** The stored coverage as a whole percentage — the same rounding the web app shows. */
export function percent(coverage: number): number {
  return Math.round(coverage * 100);
}

/** "2 of 3 matched · 1 not found · 0 uncertain", from the stored counts. */
export function axisLine(counts: { total: number; matched: number; missing: number; uncertain: number }): string {
  if (counts.total === 0) return "none stated";
  return `${counts.matched} of ${counts.total} matched · ${counts.missing} not found · ${counts.uncertain} uncertain`;
}
