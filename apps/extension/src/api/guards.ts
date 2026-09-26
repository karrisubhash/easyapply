/**
 * Enough of each answer's shape to trust that EasyApply sent it.
 *
 * The types are the generated ones (@easyapply/shared); these checks are the
 * run-time half, applied to JSON that arrived over the network from whatever
 * is listening at the web app's address. Deliberately shallow — the fields the
 * extension reads, not a second copy of the API's schema: a field the API adds
 * is not a failure here, a field the extension depends on missing is.
 */

import type {
  JobProcessingRun,
  JobRequirementsResult,
  Match,
  MatchDetail,
  ProfileVersion,
  ProfileVersionSummary,
  TailoringRun,
  TailoringRunDetail,
} from "@easyapply/shared";

type Loose = Record<string, unknown>;

function isObject(value: unknown): value is Loose {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isInt(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNullableInt(value: unknown): boolean {
  return value === null || value === undefined || isInt(value);
}

function isNullableString(value: unknown): boolean {
  return value === null || value === undefined || typeof value === "string";
}

export function isJobProcessingRun(value: unknown): value is JobProcessingRun {
  return (
    isObject(value) &&
    isInt(value.id) &&
    isInt(value.attempt) &&
    typeof value.status === "string" &&
    typeof value.is_terminal === "boolean" &&
    typeof value.can_retry === "boolean" &&
    isNullableString(value.error_code) &&
    isNullableString(value.failed_stage)
  );
}

export function isRequirementsResult(value: unknown): value is JobRequirementsResult {
  if (!isObject(value) || !isObject(value.job) || !isInt(value.job.id)) return false;
  if (value.run !== null && !isJobProcessingRun(value.run)) return false;
  const latest = value.job.latest_run;
  if (latest !== null && latest !== undefined && !isJobProcessingRun(latest)) return false;
  if (value.requirements === null || value.requirements === undefined) return true;
  return isObject(value.requirements) && isObject(value.requirements.candidate) && typeof value.requirements.structurer === "string";
}

export function isProfileVersionSummary(value: unknown): value is ProfileVersionSummary {
  return (
    isObject(value) &&
    isInt(value.id) &&
    isInt(value.version_number) &&
    (value.status === "draft" || value.status === "confirmed") &&
    typeof value.is_current === "boolean"
  );
}

export function isProfileVersionList(value: unknown): value is ProfileVersionSummary[] {
  return Array.isArray(value) && value.every(isProfileVersionSummary);
}

export function isProfileVersion(value: unknown): value is ProfileVersion {
  if (!isProfileVersionSummary(value)) return false;
  const snapshot = (value as unknown as Loose).snapshot;
  return (
    isObject(snapshot) &&
    (snapshot.contact === null || snapshot.contact === undefined || isObject(snapshot.contact)) &&
    ["links", "experiences", "education", "skills", "projects", "certifications"].every((key) => Array.isArray(snapshot[key]))
  );
}

const COUNTS = [
  "required_total",
  "required_matched",
  "required_missing",
  "required_uncertain",
  "preferred_total",
  "preferred_matched",
  "preferred_missing",
  "preferred_uncertain",
] as const;

export function isMatchSummary(value: unknown): value is Match {
  return (
    isObject(value) &&
    isInt(value.id) &&
    isInt(value.job_id) &&
    isInt(value.profile_version_id) &&
    isNullableInt(value.profile_version_number) &&
    typeof value.algorithm_version === "string" &&
    typeof value.created_at === "string" &&
    COUNTS.every((key) => isInt(value[key])) &&
    isNumber(value.required_coverage) &&
    isNumber(value.preferred_coverage) &&
    isNumber(value.overall_score)
  );
}

export function isMatchList(value: unknown): value is Match[] {
  return Array.isArray(value) && value.every(isMatchSummary);
}

function isRequirementResult(value: unknown): boolean {
  return (
    isObject(value) &&
    typeof value.category === "string" &&
    typeof value.requirement_label === "string" &&
    typeof value.is_required === "boolean" &&
    typeof value.status === "string" &&
    typeof value.explanation === "string" &&
    (value.evidence === undefined || (Array.isArray(value.evidence) && value.evidence.every(isObject)))
  );
}

export function isMatchDetail(value: unknown): value is MatchDetail {
  if (!isMatchSummary(value)) return false;
  const results = (value as unknown as Loose).requirement_results;
  return (
    (results === undefined || (Array.isArray(results) && results.every(isRequirementResult))) &&
    typeof (value as unknown as Loose).has_unmet_required === "boolean"
  );
}

export function isTailoringRun(value: unknown): value is TailoringRun {
  return (
    isObject(value) &&
    isInt(value.id) &&
    isInt(value.job_id) &&
    isInt(value.profile_version_id) &&
    isNullableInt(value.match_result_id) &&
    typeof value.status === "string" &&
    typeof value.generator === "string" &&
    typeof value.is_terminal === "boolean" &&
    isNullableString(value.grounding_status) &&
    isNullableString(value.error_code) &&
    typeof value.created_at === "string"
  );
}

export function isTailoringRunList(value: unknown): value is TailoringRun[] {
  return Array.isArray(value) && value.every(isTailoringRun);
}

export function isTailoringRunDetail(value: unknown): value is TailoringRunDetail {
  if (!isTailoringRun(value)) return false;
  const artifact = (value as unknown as Loose).artifact;
  return artifact === null || artifact === undefined || (isObject(artifact) && typeof artifact.filename === "string");
}
