/**
 * The words both clients use for the API's states — the web app and the
 * Chrome extension.
 *
 * Not part of the API contract (nothing here is generated): these are how a
 * status, a verdict or a failure code is SAID to a person. They live here, not
 * in either client, for the same reason the hand-written constants in index.ts
 * do — so that the extension's popup and the web app's pages cannot describe
 * one stored fact in two different ways. Moved verbatim out of apps/web
 * (components/job/job-status-badge.tsx, components/tailoring/
 * tailoring-status-badge.tsx, components/match/*, lib/api/match-queries.ts),
 * which now read them from here.
 *
 * Every failure string is written for the person reading it, never the
 * server's own words. A job-structuring error can quote the job description,
 * and a tailoring provider error can quote the prompt — which contains the
 * user's career history — so `error_message` is not something to show back
 * unfiltered. A client that has no entry for a code says so in its own words.
 */

import type { MatchCategory, MatchEvidenceTier, MatchStatus } from "./index";

// --- job processing ---------------------------------------------------------

export const JOB_STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  processing: "Reading the description",
  completed: "Ready to review",
  failed: "Failed",
};

export const JOB_FAILURE_HELP: Record<string, string> = {
  unexpected_error: "Processing stopped unexpectedly. Trying again may help.",
  // --- the interpretation service ---
  ai_provider_unavailable: "The interpretation service could not be reached. Try again.",
  ai_timeout: "The interpretation service took too long to answer. Try again.",
  ai_rate_limited: "The interpretation service is busy. Try again in a moment.",
  ai_provider_error: "The interpretation service failed to answer. Try again.",
  ai_output_malformed: "The interpretation was incomplete. Trying again usually resolves it.",
  ai_output_invalid: "The interpretation did not fit the schema. This needs looking at.",
  ai_content_rejected: "The interpretation service declined to process this posting.",
  // --- our configuration, not their day ---
  ai_not_configured: "AI interpretation is switched on but not configured.",
  ai_unauthorized: "AI interpretation is not correctly credentialed.",
};

/**
 * The pipeline stage a failure happened in, as a phrase that completes
 * "failed while …". An unknown value gets no phrase at all rather than the raw
 * column value: the title still says it failed, which is the fact.
 */
export const JOB_FAILED_WHILE: Record<string, string> = {
  structure: "reading the description",
  materialise: "saving the requirements",
};

/** "Processing failed while reading the description", or just "Processing failed". */
export function jobFailureTitle(run: { failed_stage?: string | null }): string {
  const phrase = run.failed_stage ? JOB_FAILED_WHILE[run.failed_stage] : undefined;
  return phrase ? `Processing failed while ${phrase}` : "Processing failed";
}

// --- tailoring --------------------------------------------------------------

export const TAILORING_STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  generating: "Writing",
  validating: "Checking against your profile",
  rendering: "Preparing the file",
  completed: "Ready",
  failed: "Failed",
};

export const TAILORING_FAILURE_HELP: Record<string, string> = {
  unexpected_error: "Generation stopped unexpectedly. Trying again may help.",
  empty_profile_version:
    "This profile version has no experience, projects, education or certifications to build a resume from.",
  document_schema_invalid: "The generated resume did not have the expected structure.",
  render_failed: "The resume was written but could not be turned into a file.",
  artifact_storage_failed: "The resume was generated but could not be saved. Try again.",
  generation_failed: "The resume could not be generated.",
  ai_provider_unavailable: "The writing service could not be reached. Try again.",
  ai_timeout: "The writing service took too long to answer. Try again.",
  ai_rate_limited: "The writing service is busy. Try again in a moment.",
  ai_provider_error: "The writing service failed to answer. Try again.",
  ai_output_malformed: "The generated resume was incomplete. Trying again usually resolves it.",
  ai_output_invalid: "The generated resume did not fit the schema. This needs looking at.",
  ai_content_rejected: "The writing service declined to generate from this content.",
  ai_not_configured: "Resume generation is not configured on this deployment.",
  ai_unauthorized: "Resume generation is not correctly credentialed.",
  ai_authentication_failed: "Resume generation is not correctly credentialed.",
  ai_billing_blocked: "The writing service account is out of credit.",
};

export const TAILORING_FAILED_WHILE: Record<string, string> = {
  generate: "writing the resume",
  validate: "checking it against your profile",
  render: "preparing the file",
};

/** "Generation failed while preparing the file", or just "Generation failed". */
export function tailoringFailureTitle(run: { failed_stage?: string | null }): string {
  const phrase = run.failed_stage ? TAILORING_FAILED_WHILE[run.failed_stage] : undefined;
  return phrase ? `Generation failed while ${phrase}` : "Generation failed";
}

// --- matching ---------------------------------------------------------------

/**
 * The three verdicts, in the words the domain uses. "Missing" is "Not found":
 * the engine may not conclude a candidate is unsuitable, only that the profile
 * does not show the thing.
 */
export const MATCH_STATUS_LABELS: Record<MatchStatus, string> = {
  matched: "Matched",
  uncertain: "Uncertain",
  missing: "Not found",
};

/** How strong each grade of evidence is, in the words a user would use. */
export const MATCH_EVIDENCE_TIER_LABELS: Record<MatchEvidenceTier, string> = {
  declared_corroborated: "Listed and demonstrated in paid work",
  employment_described: "Demonstrated in paid work",
  declared_only: "Listed in your profile",
  mentioned: "Mentioned only",
};

/** The order categories are shown in: what a person checks first, first. */
export const MATCH_CATEGORY_ORDER: readonly MatchCategory[] = [
  "skill",
  "technology",
  "experience",
  "education",
  "certification",
  "qualification",
] as const;

export const MATCH_CATEGORY_LABELS: Record<MatchCategory, string> = {
  skill: "Skills",
  technology: "Technologies",
  experience: "Experience",
  education: "Education",
  certification: "Certifications",
  qualification: "Other requirements",
};
