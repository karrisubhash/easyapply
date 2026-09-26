/**
 * A job's structured requirements, arranged for the popup.
 *
 * Arranged, not interpreted. Every row is one item of the API's `JobCandidate`
 * exactly as the structurer stored it; "required" and "preferred" are the
 * API's own split — separate lists for skills and experience, an `is_required`
 * flag on everything else — the same reading the web app's review screen makes
 * (apps/web/src/components/job/job-requirements-review.tsx). Nothing here
 * decides what a requirement means, merges two into one, or drops one.
 *
 * Each row carries its citation: the section and the exact text it was taken
 * from, and whether the server could verify that quote against the
 * description. An unverified citation is flagged, not hidden.
 */

import type { JobCandidate, JobProvenance } from "@easyapply/shared";

export type RequirementKind = "skill" | "technology" | "experience" | "education" | "certification" | "qualification";

export const REQUIREMENT_KIND_LABELS: Record<RequirementKind, string> = {
  skill: "Skill",
  technology: "Technology",
  experience: "Experience",
  education: "Education",
  certification: "Certification",
  qualification: "Qualification",
};

export interface RequirementRow {
  kind: RequirementKind;
  text: string;
  /** The structurer's own classification or bounds: a skill's category, "5+ years", a degree level. */
  detail: string | null;
  /** "Section: quote", or the quote alone. Null when the item carries no citation. */
  citation: string | null;
  /** The server could not find the quoted text in the description. */
  unverified: boolean;
}

export interface RequirementGroups {
  required: RequirementRow[];
  preferred: RequirementRow[];
  /** What the role involves. Not requirements: nothing is matched against them. */
  responsibilities: string[];
}

function citation(provenance: JobProvenance[] | undefined): Pick<RequirementRow, "citation" | "unverified"> {
  const list = provenance ?? [];
  const first = list[0];
  return {
    citation: first ? (first.source_section ? `${first.source_section}: ${first.quote}` : first.quote) : null,
    unverified: list.some((entry) => entry.verified === false),
  };
}

function years(minimum: number | null | undefined, maximum: number | null | undefined): string | null {
  if (minimum == null) return maximum == null ? null : `up to ${maximum} years`;
  return maximum == null ? `${minimum}+ years` : `${minimum}–${maximum} years`;
}

function joined(...parts: (string | null | undefined)[]): string | null {
  const kept = parts.filter((part): part is string => !!part && part.trim().length > 0);
  return kept.length > 0 ? kept.join(" · ") : null;
}

export function requirementGroups(candidate: JobCandidate): RequirementGroups {
  const required: RequirementRow[] = [];
  const preferred: RequirementRow[] = [];
  const into = (isRequired: boolean) => (isRequired ? required : preferred);

  for (const item of candidate.required_skills ?? []) {
    required.push({ kind: "skill", text: item.name, detail: item.category ?? null, ...citation(item.provenance) });
  }
  for (const item of candidate.preferred_skills ?? []) {
    preferred.push({ kind: "skill", text: item.name, detail: item.category ?? null, ...citation(item.provenance) });
  }
  for (const item of candidate.required_experience ?? []) {
    required.push({
      kind: "experience",
      text: item.description,
      detail: years(item.minimum_years, item.maximum_years),
      ...citation(item.provenance),
    });
  }
  for (const item of candidate.preferred_experience ?? []) {
    preferred.push({
      kind: "experience",
      text: item.description,
      detail: years(item.minimum_years, item.maximum_years),
      ...citation(item.provenance),
    });
  }
  for (const item of candidate.technologies ?? []) {
    into(item.is_required ?? false).push({ kind: "technology", text: item.name, detail: item.category ?? null, ...citation(item.provenance) });
  }
  for (const item of candidate.education ?? []) {
    into(item.is_required ?? false).push({
      kind: "education",
      text: item.description,
      detail: joined(item.degree_level, item.field_of_study),
      ...citation(item.provenance),
    });
  }
  for (const item of candidate.certifications ?? []) {
    into(item.is_required ?? false).push({ kind: "certification", text: item.name, detail: null, ...citation(item.provenance) });
  }
  for (const item of candidate.qualifications ?? []) {
    into(item.is_required ?? false).push({ kind: "qualification", text: item.description, detail: null, ...citation(item.provenance) });
  }

  return {
    required,
    preferred,
    responsibilities: (candidate.responsibilities ?? []).map((item) => item.description),
  };
}
