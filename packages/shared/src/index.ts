/**
 * The API contract.
 *
 * `api-schema.ts` is GENERATED from services/api openapi.json — never edit it.
 * Regenerate with `npm run api:types` at the repo root after changing a
 * Pydantic schema. These aliases exist so callers import a name rather than a
 * bracketed lookup, and so the Input/Output split that Pydantic produces for
 * Decimal fields stays visible where it matters.
 */

import type { components } from "./api-schema";

export type { components, operations, paths } from "./api-schema";

type Schemas = components["schemas"];

export type Profile = Schemas["ProfileRead"];
export type ProfileVersionSummary = Schemas["ProfileVersionSummary"];
export type ProfileVersion = Schemas["ProfileVersionRead"];
export type ConfirmVersionRequest = Schemas["ConfirmVersionRequest"];

/**
 * What the API returns. Every collection is always present, which is why this
 * is a different type from what the API accepts rather than the same one with
 * optional fields.
 */
export type Snapshot = Schemas["SnapshotRead"];
/** What the API accepts. Collections are optional and default to empty. */
export type SnapshotInput = Schemas["Snapshot"];

export type ExperienceItem = Schemas["ExperienceItem"];
export type EducationItem = Schemas["EducationItem"];
export type SkillItem = Schemas["SkillItem-Output"];
export type SkillItemInput = Schemas["SkillItem-Input"];
export type ProjectItem = Schemas["ProjectItemRead"];
export type ProjectItemInput = Schemas["ProjectItem"];
export type CertificationItem = Schemas["CertificationItem"];
export type ContactItem = Schemas["ContactItem"];
export type LinkItem = Schemas["LinkItem"];

export type VersionStatus = ProfileVersionSummary["status"];
export type DatePrecision = NonNullable<ExperienceItem["start_date_precision"]>;
export type EmploymentType = NonNullable<ExperienceItem["employment_type"]>;
export type Proficiency = NonNullable<SkillItem["proficiency"]>;

/**
 * Value sets, derived from the generated unions rather than retyped, so a
 * backend change breaks the build instead of silently disagreeing.
 */
export const DATE_PRECISIONS = ["year", "month", "day"] as const satisfies readonly DatePrecision[];
export const EMPLOYMENT_TYPES = [
  "full_time",
  "part_time",
  "contract",
  "internship",
  "freelance",
  "temporary",
] as const satisfies readonly EmploymentType[];
export const PROFICIENCIES = [
  "beginner",
  "intermediate",
  "advanced",
  "expert",
] as const satisfies readonly Proficiency[];

/** The shape of every error body the API produces (see app/api/errors.py). */
export interface ApiErrorBody {
  detail: string;
  code: string;
}

// --- resume intelligence -----------------------------------------------------

export type ResumeDocument = Schemas["ResumeDocumentRead"];
export type ResumeDocumentDetail = Schemas["ResumeDocumentDetail"];
export type ProcessingRun = Schemas["ProcessingRunRead"];
export type ProcessingEvent = Schemas["ProcessingEventRead"];
export type ProcessingResult = Schemas["ProcessingResultRead"];
export type StructuredResult = Schemas["StructuredResultRead"];
export type ResumeCandidate = Schemas["ResumeCandidate"];
export type CandidateExperience = Schemas["CandidateExperience"];
export type CandidateEducation = Schemas["CandidateEducation"];
export type CandidateSkill = Schemas["CandidateSkill"];
export type CandidateProject = Schemas["CandidateProject"];
export type CandidateCertification = Schemas["CandidateCertification"];
export type CandidateDate = Schemas["CandidateDate"];
export type Provenance = Schemas["Provenance"];
export type UnmappedItem = Schemas["UnmappedItem"];
export type ExtractedPage = Schemas["ExtractedPageRead"];

export type ProcessingStatus = ProcessingRun["status"];

/** Statuses a run can still leave. Anything else has stopped. */
export const ACTIVE_PROCESSING_STATUSES = [
  "uploaded",
  "validating",
  "extracting",
  "ocr_required",
  "ocr_processing",
  "structuring",
  "validating_result",
  "materialising",
] as const;

// --- internal operations -----------------------------------------------------
//
// Behind their own authorization boundary, not the caller's identity. These
// types carry ids, counts, timestamps and codes only: see
// services/api/app/schemas/operations.py for why every field is listed there by
// hand rather than derived from a model that already holds resume content.

export type OperatorDocument = Schemas["DocumentProcessingSummary"];
export type OperatorAttention = Schemas["AttentionList"];
export type OperatorDocumentDetail = Schemas["DocumentProcessingDetail"];
export type OperatorRun = Schemas["ProcessingRunSummary"];
export type OperatorRunDetail = Schemas["ProcessingRunDetail"];
export type OperatorStageEvent = Schemas["ProcessingStageEvent"];

/** What a document's processing looks like to whoever is responsible for it. */
export const OPERATOR_STATES = [
  "completed",
  "processing",
  "waiting_retry",
  "exhausted",
  "failed_permanent",
  "stalled",
] as const;

export type OperatorState = (typeof OPERATOR_STATES)[number];

/**
 * The states an operator is being asked to do something about.
 *
 * Mirrors NEEDS_ATTENTION in services/api/app/services/resume/operations.py.
 * The server filters; this exists so the UI can label a state without
 * re-deriving which ones matter.
 */
export const OPERATOR_ATTENTION_STATES = [
  "exhausted",
  "failed_permanent",
  "stalled",
] as const satisfies readonly OperatorState[];

export const ACCEPTED_RESUME_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
] as const;

// --- job intelligence ---------------------------------------------------
//
// The mirror of the resume aliases above. `JobIngestionRequest` is what the
// (future) extension sends; everything else is what the API returns once
// that payload has been validated, normalized, and — synchronously in this
// milestone — structured. See services/api/app/schemas/job.py for why
// `raw_payload` is deliberately not part of this surface at all.

export type JobIngestionRequest = Schemas["JobIngestionRequest"];
export type Job = Schemas["JobSummary"];
export type JobDetail = Schemas["JobDetail"];
export type JobProcessingRun = Schemas["JobProcessingRunRead"];
export type JobRequirementsResult = Schemas["JobRequirementsResponse"];
export type JobRequirements = Schemas["JobRequirementsRead"];
export type JobCandidate = Schemas["JobCandidate"];
export type JobSkillItem = Schemas["JobSkillItem"];
export type JobExperienceItem = Schemas["JobExperienceItem"];
export type JobEducationItem = Schemas["JobEducationItem"];
export type JobCertificationItem = Schemas["JobCertificationItem"];
export type JobResponsibilityItem = Schemas["JobResponsibilityItem"];
export type JobTechnologyItem = Schemas["JobTechnologyItem"];
export type JobQualificationItem = Schemas["JobQualificationItem"];
export type JobProvenance = Schemas["JobProvenance"];

export type JobProcessingStatus = JobProcessingRun["status"];

/** Statuses a job's run can still leave. Anything else has stopped. */
export const JOB_ACTIVE_PROCESSING_STATUSES = ["queued", "processing"] as const;

// --- matching -----------------------------------------------------------
//
// A match is an evaluation of one confirmed profile version against one job's
// structured requirements, by one version of the rules. The requirement-level
// results and their evidence are the useful part; `overall_score` is coverage
// of structured requirements by available profile evidence and nothing more —
// see services/api/app/services/matching/scoring.py before showing it as
// anything else.

export type MatchRequest = Schemas["MatchRequest"];
export type Match = Schemas["MatchSummary"];
export type MatchDetail = Schemas["MatchDetail"];
export type MatchRequirementResult = Schemas["MatchRequirementResultRead"];
export type MatchEvidence = Schemas["MatchEvidenceRead"];

/** The three verdicts a requirement can receive. A fourth would mean the
 * backend gained one — see app/models/match.py for why it should not. */
export const MATCH_STATUSES = ["matched", "missing", "uncertain"] as const;
export type MatchStatus = (typeof MATCH_STATUSES)[number];

/**
 * How strong a piece of evidence is, strongest first.
 *
 * Null on education and certification evidence, which is not graded — there is
 * no stronger or weaker way to hold a degree. See
 * services/api/app/services/matching/evidence.py.
 */
export const MATCH_EVIDENCE_TIERS = [
  "declared_corroborated",
  "employment_described",
  "declared_only",
  "mentioned",
] as const;
export type MatchEvidenceTier = (typeof MATCH_EVIDENCE_TIERS)[number];

/** What the posting demanded of the evidence, read from its own wording. */
export const MATCH_EVIDENCE_STANDARDS = ["any_evidence", "professional_use"] as const;
export type MatchEvidenceStandard = (typeof MATCH_EVIDENCE_STANDARDS)[number];

/** The requirement categories the engine evaluates. Responsibilities are
 * absent deliberately: they describe the role, not a bar to clear. */
export const MATCH_CATEGORIES = [
  "skill",
  "technology",
  "experience",
  "education",
  "certification",
  "qualification",
] as const;
export type MatchCategory = (typeof MATCH_CATEGORIES)[number];

// --- resume tailoring ----------------------------------------------------
//
// A tailoring run is one attempt at writing a resume for one job from one
// confirmed profile version. The RUN is the addressable resource — it exists
// from the moment Generate is pressed, so the UI has something to poll before
// there is a document.
//
// Two statuses, and they answer different questions. `status` is how far the
// pipeline got. `grounding_status` is whether we stand behind what it produced.
// A run can be `completed` with grounding `failed`: the document was generated
// successfully and every unsupported claim in it is listed — see
// services/api/app/services/tailoring/grounding.py. Only a document that passed
// has an artifact, which is what makes "downloadable" and "trustworthy" the
// same fact rather than two that can disagree.

export type TailoringRequest = Schemas["TailoringRequest"];
export type TailoringRun = Schemas["TailoringRunSummary"];
export type TailoringRunDetail = Schemas["TailoringRunDetail"];
export type TailoredResume = Schemas["TailoredResumeRead"];
export type TailoredResumeDocument = Schemas["TailoredResumeDocument"];
export type TailoredLine = Schemas["TailoredLine"];
export type TailoredContact = Schemas["TailoredContact"];
export type TailoredLink = Schemas["TailoredLink"];
export type TailoredExperience = Schemas["TailoredExperience"];
export type TailoredProject = Schemas["TailoredProject"];
export type TailoredEducation = Schemas["TailoredEducation"];
export type TailoredCertification = Schemas["TailoredCertification"];
export type TailoredSkillGroup = Schemas["TailoredSkillGroup"];
export type TailoringIssue = Schemas["TailoringIssueRead"];
export type TailoredResumeArtifact = Schemas["ArtifactRead"];

export type TailoringStatus = TailoringRun["status"];

/** Statuses a tailoring run can still leave. Anything else has stopped. */
export const TAILORING_ACTIVE_STATUSES = [
  "queued",
  "generating",
  "validating",
  "rendering",
] as const;

/**
 * Whether we stand behind the document. `failed` means at least one statement
 * in it could not be traced back to the profile version it was built from, and
 * no artifact exists — see §9. Never call a failed document "draft" or
 * "unverified" in the UI: it contains a specific claim the user cannot support,
 * and the issue list says which one.
 */
export const TAILORING_GROUNDING_STATUSES = ["passed", "failed"] as const;
export type TailoringGroundingStatus = (typeof TAILORING_GROUNDING_STATUSES)[number];

/** Errors withhold the artifact. Warnings are shown and do not. */
export const TAILORING_SEVERITIES = ["error", "warning"] as const;
export type TailoringSeverity = (typeof TAILORING_SEVERITIES)[number];

/**
 * What wrote the document. `deterministic` selects clauses verbatim out of the
 * profile and cannot fabricate; `ai` rewrites them and is held to the same
 * grounding check. Shown in the UI because they are different claims.
 */
export const TAILORING_GENERATORS = ["deterministic", "ai"] as const;
export type TailoringGenerator = (typeof TAILORING_GENERATORS)[number];

// --- the web app's session --------------------------------------------------
//
// Not part of the API: these describe the web app's own `/api/auth/*` routes,
// which both of its clients read — the web UI and the Chrome extension. Written
// by hand, like the constants above, because the web app has no OpenAPI
// document; kept here so neither client carries its own copy. See
// docs/authentication.md.

/**
 * How this web server decides who a request is for (its `AUTH_MODE`).
 *
 *   development  one configured identity (DEV_USER_EMAIL) for every request.
 *   dev-session  a signed session cookie from the development sign-in page.
 *                Per-browser, but NOT authentication: anyone can sign in as
 *                any address.
 *   entra        real sign-in with Microsoft Entra External ID; the session
 *                cookie is issued after the web server has validated the
 *                sign-in. The production mode.
 */
export const WEB_AUTH_MODES = ["development", "dev-session", "entra"] as const;
export type WebAuthMode = (typeof WEB_AUTH_MODES)[number];

/** GET /api/auth/session. Never carries the session token itself. */
export interface WebSession {
  mode: WebAuthMode;
  /** Whether requests from this browser are made for someone. The ONLY signed-in test. */
  authenticated: boolean;
  /**
   * The account's email, when known. Under real sign-in the provider may not
   * report one, so a signed-in session can have none: never read a null here
   * as "signed out". Display only — never identity.
   */
  email: string | null;
  /** The account's display name, when the identity provider gives one. Display only. */
  name?: string | null;
  /**
   * A tag for the ACCOUNT this browser is signed in to — not a credential, it
   * authenticates nothing. A client that sends it back as `X-EasyApply-Session`
   * is refused with 409 `session_changed` once the browser is signed in to a
   * different account, so a stale tab or an open extension popup can never act
   * for whoever signed in since. Signing in again as the same person keeps it.
   * Absent from servers older than it; a client treats that as "unknown".
   */
  sessionTag?: string | null;
}

/** What a signed-in account is called when the provider gave neither an email nor a name. */
export const UNNAMED_ACCOUNT_LABEL = "your EasyApply account";

/** How both clients name the signed-in account ("Saving as …"): email, else name, else a phrase. */
export function sessionLabel(session: { email?: string | null; name?: string | null }): string {
  return session.email || session.name || UNNAMED_ACCOUNT_LABEL;
}

/** The request header carrying `WebSession.sessionTag` back to the web app's proxy. */
export const SESSION_TAG_HEADER = "X-EasyApply-Session";

/** The `code` of the proxy's 409 when a request's session tag is not the current session's. */
export const SESSION_CHANGED_CODE = "session_changed";

/**
 * The `code` of the web app's 401 when a request carries no usable session.
 *
 * Distinct from the API's own `unauthenticated`: signing in cures this one, and
 * a client may offer that. The API's refusal (a production deployment refusing
 * the development identity) is not cured by anything the person can do.
 */
export const SESSION_REQUIRED_CODE = "session_required";

// --- presentation -----------------------------------------------------------
//
// How both clients SAY the states above: stage labels, verdict words, failure
// help. See ./presentation.ts for why they are shared rather than copied.

export * from "./presentation";
