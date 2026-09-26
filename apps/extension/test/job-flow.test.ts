import type { ApiFailure, ApiResult } from "../src/api/client";
import {
  canMatch,
  canTailor,
  initialFlow,
  matchForTailoring,
  reduce,
  requirementsSettled,
  sameKey,
  tailoringSettled,
  type FlowEvent,
  type FlowKey,
  type JobFlow,
} from "../src/workflow/job-flow";
import { jobDetail } from "./support/fake-api";
import { jobRun, matchDetail, requirementsFor, tailoringRun, versionSummary } from "./support/backend-fixtures";

const KEY: FlowKey = { sessionTag: "tagAda", label: "ada@example.test", tabId: 7, jobId: 1 };
const JOB = jobDetail(
  { source_type: "greenhouse", source_url: "https://job-boards.greenhouse.io/a/jobs/1", job_title: "Engineer" },
  1,
);

function ok<T>(data: T, status = 200): ApiResult<T> {
  return { ok: true, status, data };
}

function fail(kind: ApiFailure["kind"], status: number | null = null): ApiFailure {
  return { ok: false, kind, status, code: null, message: `a ${kind}` };
}

function run(flow: JobFlow, ...events: FlowEvent[]): JobFlow {
  return events.reduce(reduce, flow);
}

/** A flow with requirements ready and version `versionId` current. */
function ready(versionId = 3): JobFlow {
  return run(
    initialFlow(KEY),
    { type: "requirements", key: KEY, result: ok(requirementsFor(JOB)), checks: 1, final: false },
    { type: "profile", key: KEY, result: ok([versionSummary(versionId)]) },
  );
}

describe("requirements", () => {
  it("are ready when the backend has stored a reading", () => {
    const flow = run(initialFlow(KEY), { type: "requirements", key: KEY, result: ok(requirementsFor(JOB)), checks: 1, final: false });
    expect(flow.requirements.kind).toBe("ready");
  });

  it("are processing while the newest attempt is queued or running, never assumed finished", () => {
    for (const status of ["queued", "processing"]) {
      const running = { ...JOB, latest_run: jobRun(2, { status, is_terminal: false, finished_at: null }) };
      const answer = ok(requirementsFor(running, { stored: false }));
      expect(requirementsSettled(answer)).toBe(false);
      const flow = run(initialFlow(KEY), { type: "requirements", key: KEY, result: answer, checks: 2, final: false });
      expect(flow.requirements).toMatchObject({ kind: "processing", checks: 2, run: { status } });
    }
  });

  it("say 'still processing' — not failed, not done — when polling gave up", () => {
    const running = { ...JOB, latest_run: jobRun(2, { status: "processing", is_terminal: false }) };
    const flow = run(initialFlow(KEY), {
      type: "requirements",
      key: KEY,
      result: ok(requirementsFor(running, { stored: false })),
      checks: 8,
      final: true,
    });
    expect(flow.requirements.kind).toBe("still_processing");
  });

  it("failed when the only attempts failed and nothing was stored", () => {
    const failed = { ...JOB, latest_run: jobRun(2, { status: "failed", error_code: "unexpected_error", can_retry: true }) };
    const answer = ok(requirementsFor(failed, { stored: false }));
    expect(requirementsSettled(answer)).toBe(true);
    const flow = run(initialFlow(KEY), { type: "requirements", key: KEY, result: answer, checks: 1, final: false });
    expect(flow.requirements).toMatchObject({ kind: "failed", run: { error_code: "unexpected_error" } });
  });

  it("are still ready when a LATER re-read failed: the stored reading is what matching uses", () => {
    const refailed = { ...JOB, latest_run: jobRun(3, { attempt: 2, status: "failed" }) };
    const flow = run(initialFlow(KEY), {
      type: "requirements",
      key: KEY,
      result: ok(requirementsFor(refailed, { run: jobRun(2) })),
      checks: 1,
      final: false,
    });
    expect(flow.requirements.kind).toBe("ready");
  });

  it("are unavailable when there is no run at all", () => {
    const none = { ...JOB, latest_run: null };
    const flow = run(initialFlow(KEY), {
      type: "requirements",
      key: KEY,
      result: ok(requirementsFor(none, { run: null, stored: false })),
      checks: 1,
      final: false,
    });
    expect(flow.requirements.kind).toBe("unavailable");
  });

  it("carry the failure when the backend could not be asked", () => {
    const flow = run(initialFlow(KEY), { type: "requirements", key: KEY, result: fail("api_unreachable", 502), checks: 1, final: false });
    expect(flow.requirements).toMatchObject({ kind: "error", failure: { kind: "api_unreachable" } });
    expect(flow.session).toBe("active");
  });
});

describe("the profile version", () => {
  it.each([
    ["no profile (404)", fail("not_found", 404), "no_profile"],
    ["no versions", ok([]), "no_versions"],
    ["only drafts", ok([versionSummary(1, { status: "draft", is_current: false, confirmed_at: null })]), "drafts_only"],
    ["confirmed but none current", ok([versionSummary(1, { is_current: false })]), "no_current"],
    ["current", ok([versionSummary(1, { is_current: false }), versionSummary(2)]), "current"],
  ] as const)("%s", (_name, result, kind) => {
    const flow = run(initialFlow(KEY), { type: "profile", key: KEY, result: result as ApiResult<never> });
    expect(flow.profile.kind).toBe(kind);
  });

  it("never treats a draft as current, even if one were flagged so", () => {
    const flow = run(initialFlow(KEY), {
      type: "profile",
      key: KEY,
      result: ok([versionSummary(1, { status: "draft", is_current: true })]),
    });
    expect(flow.profile.kind).toBe("drafts_only");
    expect(flow.versionId).toBeNull();
  });

  it("throws away the match and resume of the old version when the current version changes", () => {
    const matched = run(ready(3), { type: "match_loaded", key: KEY, versionId: 3, result: ok(matchDetail(9, 1, 3)) });
    expect(matched.match.kind).toBe("matched");
    const moved = run(matched, { type: "profile", key: KEY, result: ok([versionSummary(3, { is_current: false }), versionSummary(4)]) });
    expect(moved.versionId).toBe(4);
    expect(moved.match.kind).toBe("idle");
    expect(moved.tailoring.kind).toBe("idle");
  });
});

describe("stale answers", () => {
  it("from another account, tab or job are dropped unread", () => {
    const base = ready(3);
    for (const other of [
      { ...KEY, sessionTag: "tagSomeoneElse" },
      // Same label, different account: two accounts can share an email.
      { ...KEY, sessionTag: "tagOtherAda", label: KEY.label },
      { ...KEY, sessionTag: null },
      { ...KEY, tabId: 8 },
      { ...KEY, jobId: 2 },
    ]) {
      const after = run(base, { type: "match_loaded", key: other, versionId: 3, result: ok(matchDetail(9, 1, 3)) });
      expect(after).toBe(base);
    }
  });

  it("are the same account by tag, not by what the account is called", () => {
    expect(sameKey(KEY, { ...KEY, label: "Ada Lovelace" })).toBe(true);
    expect(sameKey(KEY, { ...KEY, sessionTag: "tagOther" })).toBe(false);
  });

  it("an unknown account (no tag) is the same as no one — not even itself", () => {
    const unknown: FlowKey = { ...KEY, sessionTag: null };
    expect(sameKey(unknown, unknown)).toBe(false);
    expect(sameKey(KEY, unknown)).toBe(false);
    expect(sameKey(unknown, KEY)).toBe(false);
  });

  it("for a profile version that is no longer current are dropped unread", () => {
    const base = ready(4);
    const late = run(base, { type: "match_created", key: KEY, versionId: 3, result: ok(matchDetail(9, 1, 3), 201) });
    expect(late).toBe(base);
  });

  it("a stored match for another version is never shown as this version's", () => {
    const flow = run(ready(4), { type: "match_loaded", key: KEY, versionId: 4, result: ok(matchDetail(9, 1, 3)) });
    expect(flow.match.kind).toBe("none");
  });

  it("a created match that is not for this job and version is an error, not a result", () => {
    const flow = run(ready(4), { type: "match_created", key: KEY, versionId: 4, result: ok(matchDetail(9, 2, 4), 201) });
    expect(flow.match).toMatchObject({ kind: "error", failure: { kind: "unexpected_response" } });
  });
});

describe("matching", () => {
  it("is offered only when requirements are ready and a version is current", () => {
    expect(canMatch(initialFlow(KEY))).toBe(false);
    const flow = run(ready(3), { type: "match_loaded", key: KEY, versionId: 3, result: ok(null) });
    expect(flow.match.kind).toBe("none");
    expect(canMatch(flow)).toBe(true);
    const drafts = run(initialFlow(KEY), { type: "requirements", key: KEY, result: ok(requirementsFor(JOB)), checks: 1, final: false }, {
      type: "profile",
      key: KEY,
      result: ok([versionSummary(1, { status: "draft", is_current: false })]),
    });
    expect(canMatch(drafts)).toBe(false);
  });

  it("records whether the backend created a new evaluation or returned the stored one", () => {
    const created = run(ready(3), { type: "match_requested", key: KEY, versionId: 3 }, {
      type: "match_created",
      key: KEY,
      versionId: 3,
      result: ok(matchDetail(9, 1, 3), 201),
    });
    expect(created.match).toMatchObject({ kind: "matched", created: true });
    const existing = run(ready(3), { type: "match_created", key: KEY, versionId: 3, result: ok(matchDetail(9, 1, 3), 200) });
    expect(existing.match).toMatchObject({ kind: "matched", created: false });
    expect(matchForTailoring(existing)).toBe(9);
  });

  it("keeps the backend's verdicts exactly as sent", () => {
    const stored = matchDetail(9, 1, 3);
    const flow = run(ready(3), { type: "match_loaded", key: KEY, versionId: 3, result: ok(stored) });
    expect(flow.match.kind === "matched" && flow.match.match).toEqual(stored);
  });
});

describe("tailoring", () => {
  const loaded = (detail: ReturnType<typeof tailoringRun> | null, final = false) =>
    run(ready(3), { type: "tailoring_loaded", key: KEY, versionId: 3, result: ok(detail), checks: 2, final });

  it.each([
    ["queued", "running"],
    ["generating", "running"],
    ["validating", "running"],
    ["rendering", "running"],
  ])("a %s run is running", (status, kind) => {
    const detail = tailoringRun(5, 1, 3, { status });
    expect(tailoringSettled(ok(detail))).toBe(false);
    expect(loaded(detail).tailoring.kind).toBe(kind);
  });

  it("is still running — neither done nor failed — when polling gave up", () => {
    expect(loaded(tailoringRun(5, 1, 3, { status: "generating" }), true).tailoring.kind).toBe("still_running");
  });

  it("is ready only when completed, grounded and with a file", () => {
    expect(loaded(tailoringRun(5, 1, 3)).tailoring.kind).toBe("ready");
  });

  it("is not approved when grounding failed — no file is offered", () => {
    const withheld = tailoringRun(5, 1, 3, { grounding_status: "failed", has_artifact: false, artifact: null, error_count: 2 });
    expect(loaded(withheld).tailoring.kind).toBe("not_approved");
  });

  it("says the file is unavailable when a passed run has none", () => {
    expect(loaded(tailoringRun(5, 1, 3, { has_artifact: false, artifact: null })).tailoring.kind).toBe("no_file");
  });

  it("failed runs are failed, and retry is offered only when the backend allows it", () => {
    const failed = loaded(tailoringRun(5, 1, 3, { status: "failed", error_code: "render_failed", can_retry: true, grounding_status: null }));
    expect(failed.tailoring.kind).toBe("failed");
    expect(canTailor(failed)).toBe(true);
    const final = loaded(tailoringRun(5, 1, 3, { status: "failed", error_code: "ai_output_invalid", can_retry: false, grounding_status: null }));
    expect(canTailor(final)).toBe(false);
  });

  it("a run for another version is not this version's resume", () => {
    expect(loaded(tailoringRun(5, 1, 2)).tailoring.kind).toBe("none");
  });

  it("cannot be started twice while one is running", () => {
    const running = loaded(tailoringRun(5, 1, 3, { status: "generating" }));
    expect(canTailor(running)).toBe(false);
    const starting = run(ready(3), { type: "tailoring_requested", key: KEY, versionId: 3 });
    expect(canTailor(starting)).toBe(false);
  });
});

describe("the session", () => {
  it.each(["requirements", "profile", "match", "tailoring"])("expires on the proxy's 401 during %s", (step) => {
    const unauthorized = fail("session_required", 401);
    const event: FlowEvent =
      step === "requirements"
        ? { type: "requirements", key: KEY, result: unauthorized, checks: 1, final: false }
        : step === "profile"
          ? { type: "profile", key: KEY, result: unauthorized }
          : step === "match"
            ? { type: "match_created", key: KEY, versionId: 3, result: unauthorized }
            : { type: "tailoring_started", key: KEY, versionId: 3, result: unauthorized };
    const flow = run(ready(3), event);
    expect(flow.session).toBe("expired");
    expect(canMatch(flow)).toBe(false);
    expect(canTailor(flow)).toBe(false);
  });

  it.each(["requirements", "profile", "match", "tailoring"])(
    "changes on the proxy's 409 session_changed during %s — and everything of the account is thrown away",
    (step) => {
      const changed = fail("session_changed", 409);
      const matched = run(
        ready(3),
        { type: "match_loaded", key: KEY, versionId: 3, result: ok(matchDetail(9, 1, 3)) },
        { type: "tailoring_loaded", key: KEY, versionId: 3, result: ok(tailoringRun(5, 1, 3)), checks: 1, final: false },
      );
      expect(matched.match.kind).toBe("matched");
      const event: FlowEvent =
        step === "requirements"
          ? { type: "requirements", key: KEY, result: changed, checks: 1, final: false }
          : step === "profile"
            ? { type: "profile", key: KEY, result: changed }
            : step === "match"
              ? { type: "match_created", key: KEY, versionId: 3, result: changed }
              : { type: "tailoring_started", key: KEY, versionId: 3, result: changed };
      const flow = run(matched, event);
      expect(flow).toEqual({ ...initialFlow(KEY), session: "changed" });
      expect(canMatch(flow)).toBe(false);
      expect(canTailor(flow)).toBe(false);
    },
  );

  it("once changed, reads nothing more — a late answer for the old account is not drawn", () => {
    const changed = run(ready(3), { type: "profile", key: KEY, result: fail("session_changed", 409) });
    const late = run(
      changed,
      { type: "requirements", key: KEY, result: ok(requirementsFor(JOB)), checks: 1, final: false },
      { type: "profile", key: KEY, result: ok([versionSummary(3)]) },
    );
    expect(late).toBe(changed);
  });
});
