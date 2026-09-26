import { callKey, isApiCall, prepare, type ApiCall } from "../src/api/calls";

describe("the calls the popup may ask for", () => {
  const valid: ApiCall[] = [
    { op: "requirements", jobId: 1 },
    { op: "profileVersions" },
    { op: "currentProfileVersion" },
    { op: "matches", jobId: 2 },
    { op: "match", matchId: 3 },
    { op: "createMatch", jobId: 4, profileVersionId: 5 },
    { op: "tailoringRuns", jobId: 6 },
    { op: "tailoringRun", runId: 7 },
    { op: "startTailoring", jobId: 8, profileVersionId: 9, matchResultId: 10 },
    { op: "startTailoring", jobId: 8, profileVersionId: 9, matchResultId: null },
  ];

  it.each(valid)("accepts %j", (call) => {
    expect(isApiCall(call)).toBe(true);
  });

  it.each([
    [null],
    ["requirements"],
    [{ op: "reprocess", jobId: 1 }],
    [{ op: "fetch", path: "/internal/resume-processing/attention" }],
    [{ op: "requirements" }],
    [{ op: "requirements", jobId: 0 }],
    [{ op: "requirements", jobId: -1 }],
    [{ op: "requirements", jobId: 1.5 }],
    [{ op: "requirements", jobId: "1" }],
    [{ op: "requirements", jobId: "1/../../profile" }],
    [{ op: "requirements", jobId: Number.MAX_SAFE_INTEGER + 1 }],
    [{ op: "requirements", jobId: 1, userId: 2 }],
    [{ op: "createMatch", jobId: 1, profileVersionId: 2, email: "someone@else.test" }],
    [{ op: "createMatch", jobId: 1, profileVersionId: null }],
    [{ op: "startTailoring", jobId: 1, profileVersionId: 2 }],
    [{ op: "profileVersions", headers: { "X-Dev-User-Email": "x@y.z" } }],
  ])("refuses %j", (value) => {
    expect(isApiCall(value)).toBe(false);
  });

  it("builds each path from the checked ids alone", () => {
    expect(prepare({ op: "requirements", jobId: 12 })).toMatchObject({
      method: "GET",
      path: "/jobs/12/requirements",
      route: "GET /jobs/:id/requirements",
      write: false,
    });
    expect(prepare({ op: "tailoringRun", runId: 5 }).path).toBe("/tailored-resumes/5");
    expect(prepare({ op: "match", matchId: 9 }).path).toBe("/matches/9");
    expect(prepare({ op: "profileVersions" }).path).toBe("/profile/versions");
  });

  it("writes only the two workflow routes, with the API's own request bodies and no identity", () => {
    const match = prepare({ op: "createMatch", jobId: 4, profileVersionId: 5 });
    expect(match).toMatchObject({ method: "POST", path: "/jobs/4/matches", write: true });
    expect(match.body).toEqual({ profile_version_id: 5 });

    const tailor = prepare({ op: "startTailoring", jobId: 4, profileVersionId: 5, matchResultId: null });
    expect(tailor).toMatchObject({ method: "POST", path: "/jobs/4/tailored-resumes", write: true });
    expect(tailor.body).toEqual({ profile_version_id: 5, match_result_id: null });

    const writes = valid.map((call) => prepare(call)).filter((prepared) => prepared.write);
    expect(new Set(writes.map((prepared) => prepared.route))).toEqual(
      new Set(["POST /jobs/:id/matches", "POST /jobs/:id/tailored-resumes"]),
    );
  });

  it("refuses to build a path from something that is not an id", () => {
    expect(() => prepare({ op: "requirements", jobId: Number.NaN })).toThrow(RangeError);
  });

  it("gives equal keys only to identical requests", () => {
    expect(callKey({ op: "createMatch", jobId: 1, profileVersionId: 2 }, "tagA")).toBe(
      callKey({ profileVersionId: 2, jobId: 1, op: "createMatch" } as ApiCall, "tagA"),
    );
    expect(callKey({ op: "createMatch", jobId: 1, profileVersionId: 2 }, "tagA")).not.toBe(
      callKey({ op: "createMatch", jobId: 1, profileVersionId: 3 }, "tagA"),
    );
  });

  it("gives different keys to the same request for different accounts — ids alone do not say whose it is", () => {
    for (const call of [{ op: "profileVersions" }, { op: "currentProfileVersion" }, { op: "requirements", jobId: 1 }] as ApiCall[]) {
      expect(callKey(call, "tagA")).not.toBe(callKey(call, "tagB"));
      expect(callKey(call, "tagA")).toContain("tagA");
    }
  });

  it("gives no key at all for an account with no known tag: it is joined to nothing", () => {
    expect(callKey({ op: "profileVersions" }, null)).toBeNull();
  });
});
