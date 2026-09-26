import type {
  Match,
  MatchDetail,
  MatchEvidence,
  MatchRequirementResult,
} from "@easyapply/shared";

/**
 * Match fixtures, mirroring `job-fixtures.ts`.
 *
 * `matchDetail()` DERIVES the counts and coverage from the requirement results
 * it is given, for the same reason `run()` derives `can_retry`: the server
 * computes them from the verdicts, so a fixture that set them independently
 * could describe a result the backend can never produce — and a UI test would
 * then pass against a shape that does not exist.
 *
 * Nothing here asserts anything, and the default result deliberately contains
 * all three verdicts: a fixture that only ever matched would let an
 * uncertain-handling bug through every test that used it.
 */

export function evidence(overrides: Partial<MatchEvidence> = {}): MatchEvidence {
  return {
    source_type: "skill",
    source_id: 11,
    label: "Skill: Python",
    detail: "6 years stated",
    tier: "declared_corroborated",
    qualifies: true,
    note: "The profile lists this and paid work demonstrates it.",
    quote: null,
    ...overrides,
  };
}

let nextId = 1;

export function requirementResult(
  overrides: Partial<MatchRequirementResult> = {},
): MatchRequirementResult {
  return {
    id: nextId++,
    category: "skill",
    requirement_index: 0,
    requirement_label: "Python",
    requirement_key: "python",
    is_required: true,
    status: "matched",
    confidence: 0.95,
    explanation:
      "The profile lists python and describes paid work using it. The profile lists this and paid work demonstrates it.",
    evidence_standard: "any_evidence",
    matched_value: "python",
    evidence: [evidence()],
    ...overrides,
  };
}

/** The default set: one of each verdict on the required axis, plus a
 * preferred miss, so a component sees every case it has to render. */
export function requirementResults(): MatchRequirementResult[] {
  nextId = 1;
  return [
    requirementResult(),
    requirementResult({
      requirement_label: "Django",
      requirement_key: "django",
      requirement_index: 1,
      status: "uncertain",
      confidence: 0.5,
      explanation:
        "django appears only in the text of an experience or project, not as a listed skill. A mention is not a claim of proficiency, so this needs a person to confirm.",
      matched_value: "django",
      evidence: [
        evidence({
          source_type: "experience",
          source_id: 21,
          label: "Experience: Backend Developer at Acme Corp",
          detail: "Django REST API development",
          tier: "mentioned",
          qualifies: false,
          note: "This appears only in a project, or in a sentence describing work that was not actually carried out, so it does not show the skill was used.",
          quote: "Evaluated Django for the payments rewrite, but the project was cancelled",
        }),
      ],
    }),
    requirementResult({
      requirement_label: "Rust",
      requirement_key: "rust",
      requirement_index: 2,
      status: "missing",
      confidence: 0.9,
      explanation:
        "No skill, project technology or description in this profile version mentions rust. No qualifying evidence was found.",
      matched_value: null,
      evidence: [],
    }),
    requirementResult({
      requirement_label: "Kubernetes",
      requirement_key: "kubernetes",
      requirement_index: 3,
      is_required: false,
      status: "missing",
      confidence: 0.9,
      explanation:
        "No skill, project technology or description in this profile version mentions kubernetes. No qualifying evidence was found.",
      matched_value: null,
      evidence: [],
    }),
    requirementResult({
      category: "education",
      requirement_index: 0,
      requirement_label: "Bachelor's degree in Computer Science or related field",
      requirement_key: "bachelor",
      status: "uncertain",
      confidence: 0.4,
      explanation:
        "The profile reaches the required bachelor level, but in Mechanical Engineering rather than Computer Science. Whether that counts as a related field is not something this profile version states.",
      matched_value: "bachelor",
      evidence: [
        evidence({
          source_type: "education",
          source_id: 31,
          label: "Education: B.Tech Mechanical Engineering, Mechanical Engineering",
          detail: "State University",
          // Education evidence is not graded: there is no stronger or weaker
          // way to hold a degree.
          tier: null,
          note: null,
        }),
      ],
    }),
  ];
}

function countsFor(results: MatchRequirementResult[], required: boolean) {
  const axis = results.filter((result) => result.is_required === required);
  const matched = axis.filter((result) => result.status === "matched").length;
  return {
    total: axis.length,
    matched,
    missing: axis.filter((result) => result.status === "missing").length,
    uncertain: axis.filter((result) => result.status === "uncertain").length,
    coverage: axis.length === 0 ? 1 : matched / axis.length,
  };
}

export function matchDetail(overrides: Partial<MatchDetail> = {}): MatchDetail {
  const results = overrides.requirement_results ?? requirementResults();
  const required = countsFor(results, true);
  const preferred = countsFor(results, false);

  const base: MatchDetail = {
    id: 1,
    job_id: 1,
    // Deliberately different numbers on the same row. They agree on a
    // freshly truncated database, which is exactly why the match screen
    // could print the id under the label "Profile version" for so long.
    profile_version_id: 3,
    profile_version_number: 2,
    job_requirements_id: 7,
    algorithm_version: "matching_v5",
    as_of_date: "2026-09-19",
    created_at: "2026-09-19T10:00:00Z",
    required_total: required.total,
    required_matched: required.matched,
    required_missing: required.missing,
    required_uncertain: required.uncertain,
    preferred_total: preferred.total,
    preferred_matched: preferred.matched,
    preferred_missing: preferred.missing,
    preferred_uncertain: preferred.uncertain,
    required_coverage: required.coverage,
    preferred_coverage: preferred.coverage,
    // The documented blend: 0.8 required + 0.2 preferred. Derived rather than
    // typed so a fixture cannot state a score its own counts contradict.
    overall_score: 0.8 * required.coverage + 0.2 * preferred.coverage,
    has_unmet_required: required.missing > 0 || required.uncertain > 0,
    requirement_results: results,
  };

  return { ...base, ...overrides };
}

export function matchSummary(overrides: Partial<Match> = {}): Match {
  const { requirement_results: _results, has_unmet_required: _unmet, ...summary } =
    matchDetail();
  return { ...summary, ...overrides };
}
