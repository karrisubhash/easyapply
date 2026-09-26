import type {
  Job,
  JobDetail,
  JobProcessingRun,
  JobRequirementsResult,
} from "@easyapply/shared";

//: Failure codes the server would never call retryable — kept here rather
// than importing the real classification, since a frontend fixture must not
// depend on backend code, the same reasoning `resume-fixtures.ts` follows.
// The server's `failures.is_user_retryable`: every permanent code EXCEPT the
// configuration ones an operator can cure (`ai_not_configured`,
// `ai_unauthorized`, `ai_authentication_failed`, `ai_billing_blocked`), which
// report can_retry=true. Listing those here described a run the server can no
// longer produce, and would hide a missing Retry button for them.
const _PERMANENT_CODES = new Set([
  "ai_request_invalid",
  "ai_content_rejected",
  "ai_output_invalid",
  "structuring_failed",
]);

/**
 * Job fixtures, mirroring `resume-fixtures.ts`.
 *
 * `run()` derives `is_terminal` and `can_retry` from `status`/`error_code`,
 * the same reasoning the resume fixture gives for doing so: both are computed
 * fields on the real API response, and a fixture that set them independently
 * could describe a run the server can never actually produce.
 */
export function run(overrides: Partial<JobProcessingRun> = {}): JobProcessingRun {
  const base = {
    id: 1,
    attempt: 1,
    status: "completed",
    failed_stage: null,
    error_code: null,
    error_message: null,
    created_at: "2026-09-19T09:00:00Z",
    started_at: "2026-09-19T09:00:01Z",
    finished_at: "2026-09-19T09:00:02Z",
    ...overrides,
  };

  return {
    ...base,
    is_terminal: base.status === "completed" || base.status === "failed",
    can_retry: base.status === "failed" && !_PERMANENT_CODES.has(base.error_code ?? ""),
    ...overrides,
  };
}

export function queuedRun(overrides: Partial<JobProcessingRun> = {}): JobProcessingRun {
  return run({
    status: "queued",
    started_at: null,
    finished_at: null,
    ...overrides,
  });
}

export function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    source_type: "linkedin",
    source_url: "https://www.linkedin.com/jobs/view/123456",
    job_title: "Senior Backend Engineer",
    company_name: "Acme Corp",
    location_raw: "San Francisco, CA (Hybrid)",
    employment_type: "full_time",
    workplace_type: "hybrid",
    salary_min: 150000,
    salary_max: 190000,
    salary_currency: "USD",
    salary_period: "year",
    created_at: "2026-09-19T09:00:00Z",
    latest_run: run(),
    ...overrides,
  };
}

export function jobDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    ...job(),
    external_job_id: "123456",
    canonical_url: "https://www.linkedin.com/jobs/view/123456",
    description_text:
      "Requirements:\n- 5+ years of Python experience\n- PostgreSQL knowledge\n\nPreferred:\n- Kubernetes experience\n",
    updated_at: "2026-09-19T09:00:00Z",
    ...overrides,
  };
}

export function jobRequirementsResult(
  overrides: Partial<JobRequirementsResult> = {},
): JobRequirementsResult {
  return {
    job: job(),
    run: run(),
    requirements: {
      structurer: "deterministic",
      provider: null,
      model: null,
      prompt_version: null,
      schema_version: "1",
      created_at: "2026-09-19T09:00:02Z",
      candidate: {
        required_skills: [],
        preferred_skills: [],
        required_experience: [
          {
            description: "5+ years of Python experience",
            minimum_years: 5,
            maximum_years: null,
            provenance: [
              {
                source_section: "required",
                quote: "5+ years of Python experience",
                confidence: 0.5,
                verified: true,
              },
            ],
          },
        ],
        preferred_experience: [],
        education: [],
        certifications: [],
        responsibilities: [],
        technologies: [
          {
            name: "Python",
            category: null,
            is_required: true,
            provenance: [
              {
                source_section: "required",
                quote: "5+ years of Python experience",
                confidence: 0.5,
                verified: true,
              },
            ],
          },
          {
            name: "PostgreSQL",
            category: null,
            is_required: true,
            provenance: [
              {
                source_section: "required",
                quote: "PostgreSQL knowledge",
                confidence: 0.5,
                verified: true,
              },
            ],
          },
          {
            name: "Kubernetes",
            category: null,
            is_required: false,
            provenance: [
              {
                source_section: "preferred",
                quote: "Kubernetes experience",
                confidence: 0.5,
                verified: true,
              },
            ],
          },
        ],
        qualifications: [],
      },
    },
    ...overrides,
  };
}
