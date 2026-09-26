import { greenhouse } from "../src/adapters/greenhouse/job";
import { extractJob } from "../src/extraction/registry";
import {
  BOARD_URL,
  EXPECTED_DESCRIPTION,
  JOB_URL,
  boardDocument,
  jobDocument,
} from "./support/fixtures";

function extracted(document: Document, href = JOB_URL) {
  const result = extractJob(document, href);
  if (result.status !== "extracted") throw new Error(`expected an extraction, got ${result.status}`);
  return result;
}

describe("Greenhouse: a job posting", () => {
  it("reads every field, exactly", () => {
    const result = extracted(jobDocument());

    expect(result.site).toBe("Greenhouse");
    expect(result.warnings).toEqual([]);
    expect(result.job).toEqual({
      source_type: "greenhouse",
      source_url: JOB_URL,
      external_job_id: "4012345002",
      job_title: "Senior Backend Engineer",
      company_name: "Acme Robotics",
      location_raw: "Remote, Berlin",
      description_text: EXPECTED_DESCRIPTION,
    });
  });

  it("takes nothing from the application form, the Apply button or page scripts", () => {
    const { job } = extracted(jobDocument());
    expect(job.description_text).not.toMatch(/First Name|Submit application|Apply for this job|__remixContext|tracking/);
  });

  it("never changes the page it reads", () => {
    const document = jobDocument();
    const before = document.documentElement.outerHTML;
    extractJob(document, JOB_URL);
    expect(document.documentElement.outerHTML).toBe(before);
  });

  it("drops the fragment but keeps the query — tracking parameters are the API's to strip", () => {
    const { job } = extracted(jobDocument(), `${JOB_URL}?gh_src=abc123&gh_jid=4012345002#app`);
    expect(job.source_url).toBe(`${JOB_URL}?gh_src=abc123&gh_jid=4012345002`);
    expect(job.external_job_id).toBe("4012345002");
  });

  it("reads the EU host the same way", () => {
    const eu = "https://job-boards.eu.greenhouse.io/acmerobotics/jobs/4012345002";
    expect(extracted(jobDocument(), eu).job.source_url).toBe(eu);
  });

  it("accepts a trailing slash on the posting path", () => {
    expect(extracted(jobDocument(), `${JOB_URL}/`).job.external_job_id).toBe("4012345002");
  });
});

describe("Greenhouse: the company name", () => {
  it("keeps a company whose own name contains ' at '", () => {
    const document = jobDocument();
    document.title = "Job Application for Senior Backend Engineer at Made at Home Ltd";
    expect(extracted(document).job.company_name).toBe("Made at Home Ltd");
  });

  it("falls back to the logo's alt text when the document title is not Greenhouse's", () => {
    const document = jobDocument();
    document.title = "Careers";
    expect(extracted(document).job.company_name).toBe("Acme Robotics");
  });

  it("warns, and saves no company, when neither is there", () => {
    const document = jobDocument();
    document.title = "Careers";
    document.querySelector(".image-container")?.remove();
    const result = extracted(document);
    expect(result.job.company_name).toBeNull();
    expect(result.warnings).toEqual([
      { field: "company_name", message: "The company name was not found on the page." },
    ]);
  });
});

describe("Greenhouse: missing fields", () => {
  it("warns when the location is absent, and still extracts", () => {
    const document = jobDocument();
    document.querySelector(".job__location")?.remove();
    const result = extracted(document);
    expect(result.job.location_raw).toBeNull();
    expect(result.warnings.map((warning) => warning.field)).toEqual(["location_raw"]);
  });

  it("fails, naming the field, when the title is missing", () => {
    const document = jobDocument();
    document.querySelector(".job__title h1")?.remove();
    expect(extractJob(document, JOB_URL)).toEqual({
      status: "failed",
      site: "Greenhouse",
      missing: ["job_title"],
      message:
        "EasyApply could not find the job title on this Greenhouse page. It may still be loading, " +
        "or Greenhouse may have changed its layout. Nothing has been saved.",
    });
  });

  it("fails when the description is missing or empty", () => {
    const document = jobDocument();
    document.querySelector(".job__description")?.replaceChildren();
    const result = extractJob(document, JOB_URL);
    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.missing).toEqual(["description_text"]);
  });

  it("names both when both are missing — a layout it does not recognise", () => {
    const document = jobDocument();
    document.querySelector("main")?.replaceChildren();
    const result = extractJob(document, JOB_URL);
    expect(result.status === "failed" && result.message).toContain("the job title and job description");
  });
});

describe("Greenhouse: pages that are not one posting", () => {
  it("says a board is not a posting", () => {
    expect(extractJob(boardDocument(), BOARD_URL)).toEqual({
      status: "not_job_page",
      site: "Greenhouse",
      message: "This is a Greenhouse job board, not a single job posting. Open one posting to save it.",
    });
  });

  it("says a closed posting is closed — where Greenhouse redirects one", () => {
    const result = extractJob(boardDocument(), `${BOARD_URL}?error=true`);
    expect(result.status).toBe("not_job_page");
    expect(result.status === "not_job_page" && result.message).toMatch(/closed or no longer exists/);
  });

  it.each([
    "https://job-boards.greenhouse.io/embed/job_app?for=acmerobotics&token=4012345002",
    "https://job-boards.greenhouse.io/acmerobotics/jobs/not-a-number",
    "https://job-boards.greenhouse.io/acmerobotics/jobs/4012345002/apply",
  ])("does not treat %s as a posting", (href) => {
    expect(extractJob(jobDocument(), href).status).toBe("not_job_page");
  });
});

describe("Greenhouse: which URLs are Greenhouse at all", () => {
  it.each([
    ["https://job-boards.greenhouse.io/acmerobotics/jobs/1", true],
    ["https://job-boards.eu.greenhouse.io/acmerobotics/jobs/1", true],
    ["http://job-boards.greenhouse.io/acmerobotics/jobs/1", false],
    ["https://boards.greenhouse.io/acmerobotics/jobs/1", false],
    ["https://job-boards.greenhouse.io.evil.example/acmerobotics/jobs/1", false],
    ["https://careers.example.com/?gh_jid=1", false],
  ])("%s -> %s", (href, expected) => {
    expect(greenhouse.handles(new URL(href))).toBe(expected);
  });
});
