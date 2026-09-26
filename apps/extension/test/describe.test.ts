import type { ApiFailure } from "../src/api/client";
import type { SubmitOutcome } from "../src/api/jobs";
import type { JobIngestionRequest } from "../src/contract";
import {
  describeFailure,
  describeOutcome,
  describeSession,
  describeSessionChanged,
  describeSessionExpired,
  differencesFromSaved,
} from "../src/popup/describe";
import { jobDetail } from "./support/fake-api";
import { EXTENSION_ID } from "./support/fake-chrome";
import { JOB_URL } from "./support/fixtures";

const PAYLOAD: JobIngestionRequest = {
  source_type: "greenhouse",
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: "Build things.",
};

const CONTEXT = { webUrl: "http://localhost:3000", extensionId: EXTENSION_ID };

function describe_(outcome: SubmitOutcome) {
  return describeOutcome(outcome, PAYLOAD, CONTEXT);
}

describe("what the popup says", () => {
  it("a new job, and that its requirements were read", () => {
    expect(describe_({ kind: "created", job: jobDetail(PAYLOAD, 1) })).toEqual({
      tone: "success",
      title: "Saved to EasyApply.",
      lines: ["EasyApply has read its requirements."],
    });
  });

  it("a new job whose structuring failed says so, rather than claiming success for both", () => {
    const job = jobDetail(PAYLOAD, 1);
    job.latest_run = { ...job.latest_run!, status: "failed" };
    expect(describe_({ kind: "created", job }).lines[0]).toMatch(/reading its requirements failed/);
  });

  it("an existing job: nothing changed, and when it was saved", () => {
    expect(describe_({ kind: "existing", job: jobDetail(PAYLOAD, 1) })).toEqual({
      tone: "info",
      title: "Already in EasyApply.",
      lines: [
        "EasyApply already had this posting, saved Sep 26, 2026. It keeps the version it saved first, so nothing was added or changed.",
      ],
    });
  });

  it("an existing job saved differently: which reviewed values were not applied", () => {
    const saved = jobDetail({ ...PAYLOAD, job_title: "Backend Engineer", description_text: "Older text." }, 1);
    expect(describe_({ kind: "existing", job: saved }).lines[1]).toBe(
      "The saved version differs from what you reviewed in: title, description.",
    );
  });

  it("a refused extension is told the exact line that allows it", () => {
    const notice = describe_({
      kind: "rejected",
      status: 403,
      code: "cross_site_request",
      message: "Cross-site requests are not accepted.",
      issues: [],
    });
    expect(notice.lines).toEqual([
      "Add this line to apps/web/.env.local, then restart the web app:",
      `PROXY_ALLOWED_EXTENSION_IDS=${EXTENSION_ID}`,
    ]);
  });

  it("field refusals name the field, not FastAPI's location", () => {
    const notice = describe_({
      kind: "rejected",
      status: 422,
      code: "request_validation",
      message: "EasyApply refused some of the fields.",
      issues: [{ loc: ["body", "job_title"], msg: "String should have at most 500 characters", type: "string_too_long" }],
    });
    expect(notice.lines).toEqual(["job_title: String should have at most 500 characters"]);
  });

  it("any other refusal carries the server's words and code", () => {
    const notice = describe_({ kind: "rejected", status: 401, code: "unauthenticated", message: "No sign-in.", issues: [] });
    expect(notice.lines).toEqual(["No sign-in. (unauthenticated, HTTP 401)"]);
  });

  it.each([
    ["network", /Nothing answered at http:\/\/localhost:3000/],
    ["api_unreachable", /the API behind it did not/],
  ] as const)("unreachable (%s) says which part is down, and that nothing was saved", (code, pattern) => {
    const notice = describe_({ kind: "unreachable", code, message: "" });
    expect(notice.tone).toBe("error");
    expect(notice.lines.join(" ")).toMatch(pattern);
    expect(notice.lines.join(" ")).toMatch(/Nothing was saved/);
  });
});

describe("differencesFromSaved", () => {
  it("is empty when the saved job is what was reviewed", () => {
    expect(differencesFromSaved(PAYLOAD, jobDetail(PAYLOAD, 1))).toEqual([]);
  });

  it("treats a missing value and null as the same", () => {
    const withoutCompany: JobIngestionRequest = { ...PAYLOAD };
    delete withoutCompany.company_name;
    expect(differencesFromSaved(withoutCompany, jobDetail({ ...PAYLOAD, company_name: null }, 1))).toEqual([]);
  });
});

describe("the account a save would land in", () => {
  it("signed in: nothing to warn about — the account is shown as it is", () => {
    expect(describeSession({ kind: "signed_in", mode: "dev-session", label: "a@b.test", sessionTag: "tagA" }, CONTEXT)).toBeNull();
  });

  it("signed out: says nothing is sent, and links to sign in", () => {
    const notice = describeSession({ kind: "signed_out" }, CONTEXT);
    expect(notice?.title).toBe("Sign in to EasyApply to save this job.");
    expect(notice?.lines.join(" ")).toMatch(/Nothing is sent until you sign in/);
    expect(notice?.link).toEqual({ href: "http://localhost:3000/", text: "Sign in to EasyApply", id: "sign-in" });
  });

  it("unknown: says so, without claiming either way", () => {
    const notice = describeSession({ kind: "unknown", message: "Could not reach EasyApply." }, CONTEXT);
    expect(notice?.title).toBe("Couldn't check which account this would be saved to.");
    expect(notice?.link).toBeUndefined();
  });

  it("a save refused for want of a session says nothing was saved, and links to sign in", () => {
    const notice = describe_({
      kind: "rejected",
      status: 401,
      code: "session_required",
      message: "You are not signed in to EasyApply.",
      issues: [],
    });
    expect(notice.title).toBe("You're not signed in to EasyApply.");
    expect(notice.lines).toEqual(["Nothing was saved. Sign in, then open EasyApply again on this page."]);
    expect(notice.link?.href).toBe("http://localhost:3000/");
    // Not "sign-in": the account notice above carries that id at the same time.
    expect(notice.link?.id).toBe("session-sign-in");
  });

  it("a save refused because the browser is signed in to someone else now says exactly that", () => {
    const notice = describe_({
      kind: "rejected",
      status: 409,
      code: "session_changed",
      message: "This browser is signed in to EasyApply as a different account now.",
      issues: [],
    });
    expect(notice).toEqual(describeSessionChanged());
  });
});

function failure(kind: ApiFailure["kind"], status: number | null, code: string | null = null): ApiFailure {
  return { ok: false, kind, status, code, message: `the server's own words for ${kind}` };
}

describe("when the session is gone mid-flow", () => {
  it("expired (the proxy's 401 after a session was known): the brief's exact words, and a way to sign in", () => {
    const notice = describeFailure(failure("session_required", 401, "session_required"), "match your profile", CONTEXT);
    expect(notice).toEqual({
      tone: "error",
      title: "Your EasyApply session expired. Sign in again.",
      lines: ["EasyApply couldn't match your profile. Once you've signed in, open EasyApply on this page again."],
      link: { href: "http://localhost:3000/", text: "Sign in to EasyApply", id: "session-sign-in" },
    });
    expect(describeSessionExpired("save this job", CONTEXT).title).toBe("Your EasyApply session expired. Sign in again.");
  });

  it("changed (the proxy's 409 session_changed): names the switch and how to continue, and nothing of either account", () => {
    const notice = describeFailure(failure("session_changed", 409, "session_changed"), "match your profile", CONTEXT);
    expect(notice).toEqual({
      tone: "error",
      title: "You're signed in to EasyApply as a different account now.",
      lines: [
        "This popup was for the account signed in before, so EasyApply has cleared it and done nothing more. " +
          "Reload to continue as the account signed in now.",
      ],
    });
    expect(notice.lines.join(" ")).not.toContain("the server's own words");
    expect(JSON.stringify(notice)).not.toMatch(/@/);
  });

  it("a plain 409 is still the resource refusing, in the API's words — not a session change", () => {
    const notice = describeFailure(failure("conflict", 409, "conflict"), "match your profile", CONTEXT);
    expect(notice.title).toBe("EasyApply couldn't match your profile yet.");
  });
});
