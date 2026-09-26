/**
 * Answers the EasyApply API gives, as typed values.
 *
 * Every object here is checked by the compiler against the GENERATED contract
 * (@easyapply/shared, from the API's OpenAPI document), so a renamed or retyped
 * field fails `tsc` rather than silently testing a shape the API no longer
 * sends. The values are canned: a verdict here is what a test says the backend
 * answered, never something computed — the extension must render whatever the
 * backend says, so the tests choose what that is.
 *
 * Shared by the Vitest suites (./fake-api.ts) and the browser suite's stub web
 * app (e2e/stub-server.ts).
 */

import type {
  JobCandidate,
  JobDetail,
  JobProcessingRun,
  JobRequirementsResult,
  Match,
  MatchDetail,
  ProfileVersion,
  ProfileVersionSummary,
  Snapshot,
  TailoringRun,
  TailoringRunDetail,
} from "@easyapply/shared";

export const AT = "2026-09-26T09:00:00Z";

export function jobRun(id: number, overrides: Partial<JobProcessingRun> = {}): JobProcessingRun {
  return {
    id,
    attempt: 1,
    status: "completed",
    failed_stage: null,
    error_code: null,
    error_message: null,
    created_at: AT,
    started_at: AT,
    finished_at: "2026-09-26T09:00:01Z",
    is_terminal: true,
    can_retry: false,
    ...overrides,
  };
}

/** What the structurer read from test/fixtures/greenhouse-job.html — as a test says it did. */
export const CANDIDATE: JobCandidate = {
  required_skills: [
    {
      name: "SQL",
      category: "database",
      provenance: [{ source_section: "What you'll bring", quote: "Strong knowledge of SQL and FastAPI.", confidence: 0.9, verified: true }],
    },
    {
      name: "FastAPI",
      category: "framework",
      provenance: [{ source_section: "What you'll bring", quote: "Strong knowledge of SQL and FastAPI.", confidence: 0.9, verified: true }],
    },
  ],
  preferred_skills: [
    {
      name: "Kubernetes",
      category: "platform",
      provenance: [{ source_section: "Nice to have", quote: "Kubernetes", confidence: 0.8, verified: true }],
    },
  ],
  required_experience: [
    {
      description: "5+ years of backend development",
      minimum_years: 5,
      maximum_years: null,
      provenance: [
        { source_section: "What you'll bring", quote: "Experience - 5+ years of backend development.", confidence: 0.9, verified: true },
      ],
    },
  ],
  preferred_experience: [],
  education: [
    {
      description: "Bachelor's degree in Computer Science",
      degree_level: "bachelor",
      field_of_study: "Computer Science",
      is_required: false,
      provenance: [{ source_section: null, quote: "Bachelor's degree in Computer Science", confidence: 0.6, verified: false }],
    },
  ],
  certifications: [],
  responsibilities: [
    {
      description: "Design and operate Python services on PostgreSQL.",
      provenance: [{ source_section: "What you'll do", quote: "Design and operate Python services on PostgreSQL.", confidence: 0.9, verified: true }],
    },
  ],
  technologies: [
    {
      name: "Terraform",
      category: "infrastructure",
      is_required: false,
      provenance: [{ source_section: "Nice to have", quote: "Terraform", confidence: 0.8, verified: true }],
    },
  ],
  qualifications: [],
};

export function jobSummaryOf(job: JobDetail): JobRequirementsResult["job"] {
  return {
    id: job.id,
    source_type: job.source_type,
    source_url: job.source_url,
    job_title: job.job_title,
    company_name: job.company_name,
    location_raw: job.location_raw,
    employment_type: job.employment_type,
    workplace_type: job.workplace_type,
    salary_min: job.salary_min,
    salary_max: job.salary_max,
    salary_currency: job.salary_currency,
    salary_period: job.salary_period,
    created_at: job.created_at,
    latest_run: job.latest_run ?? null,
  };
}

export function requirementsFor(
  job: JobDetail,
  { run = job.latest_run ?? null, candidate = CANDIDATE, stored = true }: { run?: JobProcessingRun | null; candidate?: JobCandidate; stored?: boolean } = {},
): JobRequirementsResult {
  return {
    job: jobSummaryOf(job),
    run,
    requirements: stored
      ? {
          structurer: "deterministic",
          provider: null,
          model: null,
          prompt_version: null,
          schema_version: "job_candidate_v1",
          candidate,
          created_at: AT,
        }
      : null,
  };
}

// --- profile ---------------------------------------------------------------------

export const SNAPSHOT: Snapshot = {
  contact: {
    full_name: "Ada Lovelace",
    email: "ada@example.test",
    phone: "+44 20 7946 0000",
    location: "London, United Kingdom",
  },
  summary: "Backend engineer.",
  links: [
    { url: "https://www.linkedin.com/in/ada-lovelace", label: "LinkedIn" },
    { url: "https://github.com/ada", label: "GitHub" },
    { url: "https://ada.example.test", label: "Portfolio" },
  ],
  experiences: [
    {
      company: "Analytical Engines Ltd",
      job_title: "Backend Engineer",
      employment_type: "full_time",
      location: "London",
      start_date: "2019-01-01",
      start_date_precision: "month",
      end_date: null,
      end_date_precision: null,
      description: "Built Python services on PostgreSQL.",
    },
  ],
  education: [],
  skills: [
    { name: "Python", category: "language", proficiency: "expert", years_of_experience: null },
    { name: "SQL", category: "database", proficiency: null, years_of_experience: null },
  ],
  projects: [],
  certifications: [],
};

export function versionSummary(id: number, overrides: Partial<ProfileVersionSummary> = {}): ProfileVersionSummary {
  return {
    id,
    version_number: id,
    status: "confirmed",
    is_current: true,
    created_by_type: "user",
    created_at: AT,
    confirmed_at: AT,
    ...overrides,
  };
}

export function profileVersion(summary: ProfileVersionSummary, snapshot: Snapshot = SNAPSHOT): ProfileVersion {
  return { ...summary, snapshot };
}

// --- matching --------------------------------------------------------------------

export function matchSummary(id: number, jobId: number, versionId: number, overrides: Partial<Match> = {}): Match {
  return {
    id,
    job_id: jobId,
    profile_version_id: versionId,
    profile_version_number: versionId,
    job_requirements_id: jobId * 100,
    algorithm_version: "matching_v5_1",
    as_of_date: "2026-09-26",
    created_at: AT,
    required_total: 3,
    required_matched: 1,
    required_missing: 1,
    required_uncertain: 1,
    preferred_total: 3,
    preferred_matched: 0,
    preferred_missing: 3,
    preferred_uncertain: 0,
    required_coverage: 0.3333,
    preferred_coverage: 0,
    overall_score: 0.25,
    ...overrides,
  };
}

/** A stored match. Its verdicts are chosen here, by the test — not computed. */
export function matchDetail(id: number, jobId: number, versionId: number, overrides: Partial<MatchDetail> = {}): MatchDetail {
  return {
    ...matchSummary(id, jobId, versionId),
    has_unmet_required: true,
    requirement_results: [
      {
        id: id * 10 + 1,
        category: "skill",
        requirement_index: 0,
        requirement_label: "SQL",
        requirement_key: "sql",
        is_required: true,
        status: "matched",
        confidence: 0.9,
        explanation: "Your profile lists SQL.",
        evidence_standard: "any_evidence",
        matched_value: "SQL",
        evidence: [
          {
            source_type: "skill",
            source_id: 2,
            label: "SQL",
            detail: "Skill",
            tier: "declared_only",
            qualifies: true,
            note: null,
            quote: null,
          },
        ],
      },
      {
        id: id * 10 + 2,
        category: "skill",
        requirement_index: 1,
        requirement_label: "FastAPI",
        requirement_key: "fastapi",
        is_required: true,
        status: "missing",
        confidence: 0,
        explanation: "Nothing in your profile mentions FastAPI.",
        evidence_standard: "any_evidence",
        matched_value: null,
        evidence: [],
      },
      {
        id: id * 10 + 3,
        category: "experience",
        requirement_index: 0,
        requirement_label: "5+ years of backend development",
        requirement_key: null,
        is_required: true,
        status: "uncertain",
        confidence: 0.4,
        explanation: "Your profile shows backend work, but not five years of it that EasyApply can count.",
        evidence_standard: "professional_use",
        matched_value: null,
        evidence: [
          {
            source_type: "experience",
            source_id: 1,
            label: "Backend Engineer at Analytical Engines Ltd",
            detail: "2019 – present",
            tier: "employment_described",
            qualifies: false,
            note: "Dates could not be counted as five full years.",
            quote: "Built Python services on PostgreSQL.",
          },
        ],
      },
      {
        id: id * 10 + 4,
        category: "skill",
        requirement_index: 2,
        requirement_label: "Kubernetes",
        requirement_key: "kubernetes",
        is_required: false,
        status: "missing",
        confidence: 0,
        explanation: "Nothing in your profile mentions Kubernetes.",
        evidence_standard: "any_evidence",
        matched_value: null,
        evidence: [],
      },
    ],
    ...overrides,
  };
}

// --- tailoring -------------------------------------------------------------------

export function tailoringRun(
  id: number,
  jobId: number,
  versionId: number,
  overrides: Partial<TailoringRunDetail> = {},
): TailoringRunDetail {
  const status = overrides.status ?? "completed";
  const completed = status === "completed";
  return {
    id,
    job_id: jobId,
    profile_version_id: versionId,
    job_requirements_id: jobId * 100,
    match_result_id: null,
    attempt: 1,
    status,
    generator: "deterministic",
    model: null,
    prompt_version: null,
    failed_stage: null,
    error_code: null,
    error_message: null,
    created_at: AT,
    started_at: AT,
    finished_at: completed ? "2026-09-26T09:00:05Z" : null,
    is_stalled: false,
    can_retry: false,
    profile_version_number: versionId,
    job_title: "Senior Backend Engineer",
    company_name: "Acme Robotics",
    grounding_status: completed ? "passed" : null,
    error_count: completed ? 0 : null,
    warning_count: completed ? 0 : null,
    has_artifact: completed,
    result: null,
    artifact: completed
      ? {
          filename: "Ada_Lovelace_Acme_Robotics_v1.docx",
          media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          byte_size: 12_345,
          created_at: AT,
        }
      : null,
    is_terminal: status === "completed" || status === "failed",
    ...overrides,
  };
}

export function tailoringSummary(run: TailoringRunDetail): TailoringRun {
  const summary: Partial<TailoringRunDetail> = { ...run };
  delete summary.result;
  delete summary.artifact;
  return summary as TailoringRun;
}
