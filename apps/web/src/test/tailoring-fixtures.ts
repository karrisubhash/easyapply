import type {
  TailoredResume,
  TailoredResumeDocument,
  TailoringIssue,
  TailoringRun,
  TailoringRunDetail,
} from "@easyapply/shared";

/**
 * Tailoring fixtures, mirroring `match-fixtures.ts`.
 *
 * `tailoredResume()` DERIVES `grounding_status`, `error_count`, `warning_count`
 * and `is_approved` from the issues it is given — the server cannot produce a
 * document that is approved AND carries an error (a CHECK constraint forbids
 * it), so a fixture that set those independently could describe a state the
 * backend will never return, and a UI test would pass against a shape that does
 * not exist.
 *
 * The same discipline applies to `has_artifact` on a run: it is true exactly
 * when the document passed, because that is the rule the pipeline enforces.
 */

export function tailoredLine(text: string, quote = text) {
  return { text, source_quote: quote, source_field: "description" };
}

export function tailoredDocument(
  overrides: Partial<TailoredResumeDocument> = {},
): TailoredResumeDocument {
  return {
    // Who the resume is from. Every field is a column on the profile version,
    // copied verbatim — the API sends it because a document without it is not a
    // resume anyone can reply to.
    contact: {
      full_name: "Alex Morgan",
      email: "alex@example.com",
      phone: "+44 7700 900123",
      location: "Bristol, United Kingdom",
      links: [
        { url: "https://www.linkedin.com/in/alex-morgan", label: "LinkedIn" },
        { url: "https://github.com/alex-morgan", label: "GitHub" },
      ],
    },
    headline: {
      text: "Senior Backend Engineer",
      source_quote: "Senior Backend Engineer",
      source_field: "job_title",
    },
    summary: [tailoredLine("Built the payments ingestion service in Python.")],
    skills: [{ category: "Relevant to this role", skills: ["Python", "PostgreSQL"] }],
    experience: [
      {
        source_id: 101,
        company: "Acme Corp",
        job_title: "Senior Backend Engineer",
        location: "Berlin",
        start_date: "2021-03-01",
        start_date_precision: "month",
        end_date: "2024-06-01",
        end_date_precision: "month",
        bullets: [
          tailoredLine("Built the payments ingestion service in Python."),
          // A line whose wording differs from its source, so the
          // ORIGINAL -> TAILORED display has something to actually show.
          tailoredLine(
            "Delivered the reporting pipeline migration onto PostgreSQL.",
            "Migrated the reporting pipeline onto PostgreSQL with zero downtime.",
          ),
        ],
      },
    ],
    projects: [
      {
        source_id: 201,
        name: "EasyApply",
        role: null,
        technologies: ["FastAPI", "PostgreSQL"],
        bullets: [],
      },
    ],
    education: [
      {
        source_id: 401,
        institution: "State University",
        degree: "BSc",
        field_of_study: "Computer Science",
        start_date: null,
        end_date: null,
      },
    ],
    certifications: [],
    targeted_requirements: ["Python", "PostgreSQL"],
    ...overrides,
  };
}

export function tailoringIssue(overrides: Partial<TailoringIssue> = {}): TailoringIssue {
  return {
    severity: "error",
    section: "experience",
    field_path: "experience[0].bullets[0]",
    claim: "Ran the team's on-call rotation for 40 engineers.",
    reason: "This line states a number the text it cites does not contain.",
    ...overrides,
  };
}

export function tailoredResume(
  overrides: Partial<TailoredResume> = {},
): TailoredResume {
  const issues = overrides.issues ?? [];
  const errors = issues.filter((issue) => issue.severity === "error").length;
  const warnings = issues.filter((issue) => issue.severity === "warning").length;

  return {
    document: tailoredDocument(),
    schema_version: "1",
    // Derived, never set independently: the server's own CHECK constraint says
    // passed means zero errors.
    grounding_status: errors === 0 ? "passed" : "failed",
    is_approved: errors === 0,
    error_count: errors,
    warning_count: warnings,
    created_at: "2026-09-21T12:00:00Z",
    ...overrides,
    issues,
  };
}

export function tailoringRun(overrides: Partial<TailoringRun> = {}): TailoringRun {
  return {
    id: 1,
    job_id: 7,
    profile_version_id: 102,
    job_requirements_id: 3,
    match_result_id: null,
    attempt: 1,
    status: "completed",
    generator: "deterministic",
    model: null,
    prompt_version: "tailoring_v1",
    failed_stage: null,
    error_code: null,
    error_message: null,
    created_at: "2026-09-21T12:00:00Z",
    started_at: "2026-09-21T12:00:01Z",
    finished_at: "2026-09-21T12:00:04Z",
    is_terminal: true,
    is_stalled: false,
    can_retry: false,
    profile_version_number: 1,
    job_title: "Senior Backend Engineer",
    company_name: "Acme Corp",
    grounding_status: "passed",
    error_count: 0,
    warning_count: 0,
    has_artifact: true,
    ...overrides,
  };
}

export function tailoringRunDetail(
  overrides: Partial<TailoringRunDetail> = {},
): TailoringRunDetail {
  const result = "result" in overrides ? overrides.result : tailoredResume();
  const approved = result?.is_approved ?? false;

  return {
    ...tailoringRun(),
    grounding_status: result?.grounding_status ?? null,
    error_count: result?.error_count ?? null,
    warning_count: result?.warning_count ?? null,
    // The artifact exists exactly when the document passed. Two facts the
    // backend keeps in step by construction; a fixture that let them drift
    // would let a broken download gate pass its test.
    has_artifact: approved,
    artifact: approved
      ? {
          filename: "Acme-Corp-Senior-Backend-Engineer-v1.docx",
          media_type:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          byte_size: 18_204,
          created_at: "2026-09-21T12:00:04Z",
        }
      : null,
    ...overrides,
    result,
  };
}

/** A run still being worked on, for the states the UI has to show while waiting. */
export function tailoringRunInFlight(
  status: TailoringRun["status"] = "generating",
): TailoringRunDetail {
  return tailoringRunDetail({
    status,
    is_terminal: false,
    finished_at: null,
    grounding_status: null,
    error_count: null,
    warning_count: null,
    has_artifact: false,
    artifact: null,
    result: null,
  });
}
