import type { ExtractedJob } from "../src/extraction/types";
import { buildPayload, codePointLength, draftFrom, requestBody } from "../src/review/payload";
import { EXPECTED_DESCRIPTION, JOB_URL } from "./support/fixtures";

const JOB: ExtractedJob = {
  source_type: "greenhouse",
  source_url: JOB_URL,
  external_job_id: "4012345002",
  job_title: "Senior Backend Engineer",
  company_name: "Acme Robotics",
  location_raw: "Remote, Berlin",
  description_text: EXPECTED_DESCRIPTION,
};

describe("the reviewed payload", () => {
  it("is exactly the extraction when nothing was edited, in a fixed key order", () => {
    const { payload, issues, sendable } = buildPayload(JOB, draftFrom(JOB));
    expect(payload).toEqual({ ...JOB });
    expect(Object.keys(payload)).toEqual([
      "source_type",
      "source_url",
      "external_job_id",
      "job_title",
      "company_name",
      "location_raw",
      "description_text",
    ]);
    expect(issues).toEqual([]);
    expect(sendable).toBe(true);
  });

  it("never carries identity, ownership or status — nothing but the posting", () => {
    const { payload } = buildPayload(JOB, draftFrom(JOB));
    for (const forbidden of ["user_id", "user", "email", "owner", "status", "id"]) {
      expect(payload).not.toHaveProperty(forbidden);
    }
  });

  it("applies the person's edits, cleaned the way the API cleans them", () => {
    const { payload } = buildPayload(JOB, {
      job_title: "  Staff   Backend Engineer ",
      company_name: "Acme\u00a0Robotics GmbH",
      location_raw: "Berlin,\tGermany",
      description_text: "Line one  \r\n\r\n\r\nLine two\u0000",
    });
    expect(payload.job_title).toBe("Staff Backend Engineer");
    expect(payload.company_name).toBe("Acme Robotics GmbH");
    expect(payload.location_raw).toBe("Berlin, Germany");
    expect(payload.description_text).toBe("Line one\n\nLine two");
  });

  it("keeps the identity fields from the page whatever the draft says", () => {
    const draft = { ...draftFrom(JOB), source_url: "https://evil.example", external_job_id: "1" };
    const { payload } = buildPayload(JOB, draft);
    expect(payload.source_url).toBe(JOB_URL);
    expect(payload.external_job_id).toBe("4012345002");
    expect(payload.source_type).toBe("greenhouse");
  });

  it("sends an emptied optional field as null, not as an empty string", () => {
    const { payload, sendable } = buildPayload(JOB, { ...draftFrom(JOB), company_name: "  ", location_raw: "" });
    expect(payload.company_name).toBeNull();
    expect(payload.location_raw).toBeNull();
    expect(sendable).toBe(true);
  });

  it("refuses to send without a title", () => {
    const { issues, sendable } = buildPayload(JOB, { ...draftFrom(JOB), job_title: " \n " });
    expect(sendable).toBe(false);
    expect(issues).toContainEqual({ field: "job_title", severity: "error", message: "A job title is required." });
  });

  it("warns, but allows, a job saved without a description", () => {
    const { payload, issues, sendable } = buildPayload(JOB, { ...draftFrom(JOB), description_text: "" });
    expect(payload.description_text).toBeNull();
    expect(sendable).toBe(true);
    expect(issues).toHaveLength(1);
    expect(issues[0]).toMatchObject({ field: "description_text", severity: "warning" });
  });

  it("refuses an over-long field instead of cutting it", () => {
    const description = "x".repeat(50_001);
    const { payload, issues, sendable } = buildPayload(JOB, { ...draftFrom(JOB), description_text: description });
    expect(sendable).toBe(false);
    expect(payload.description_text).toBe(description);
    expect(issues).toContainEqual({
      field: "description_text",
      severity: "error",
      message:
        "The description is 50,001 characters; EasyApply accepts at most 50,000. Nothing is cut automatically — shorten it before saving.",
    });
  });

  it("accepts a field at exactly its limit", () => {
    expect(buildPayload(JOB, { ...draftFrom(JOB), job_title: "t".repeat(500) }).sendable).toBe(true);
    expect(buildPayload(JOB, { ...draftFrom(JOB), job_title: "t".repeat(501) }).sendable).toBe(false);
  });

  it("counts characters as the API does: an emoji is one, not two", () => {
    expect(codePointLength("🚀")).toBe(1);
    expect("🚀".length).toBe(2);
    const title = "🚀".repeat(500);
    expect(buildPayload(JOB, { ...draftFrom(JOB), job_title: title }).sendable).toBe(true);
  });

  it("refuses a page address the API would refuse", () => {
    const long = { ...JOB, source_url: `${JOB_URL}?q=${"a".repeat(2048)}` };
    expect(buildPayload(long, draftFrom(long)).issues.map((issue) => issue.field)).toContain("source_url");
    const file = { ...JOB, source_url: "file:///C:/jobs.html" };
    expect(buildPayload(file, draftFrom(file)).sendable).toBe(false);
  });

  it("sends the body the preview shows", () => {
    const { payload } = buildPayload(JOB, draftFrom(JOB));
    expect(JSON.parse(requestBody(payload))).toEqual(payload);
    expect(requestBody(payload)).toBe(JSON.stringify(payload));
  });
});
