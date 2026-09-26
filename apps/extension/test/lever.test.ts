/**
 * Lever: the second site, and the proof that the adapter interfaces are
 * sufficient. Its posting and form differ from Greenhouse's in every way the
 * interfaces had to absorb — the form on a page of its own, controls keyed by
 * name, labels in sibling blocks, native radio/checkbox/select groups — and no
 * code outside src/adapters/lever/ knows any of it.
 */

import { leverApplication } from "../src/adapters/lever/application";
import { answerContentMessage } from "../src/content/handler";
import { extractJob } from "../src/extraction/registry";
import { fillForm } from "../src/forms/fill";
import { detectForm } from "../src/forms/registry";
import type { FieldDescriptor } from "../src/forms/types";
import { proposeAll } from "../src/profile/mapping";
import { DETECT_FORM } from "../src/shared/messages";
import { SNAPSHOT } from "./support/backend-fixtures";
import { fixtureHtml, parseDocument } from "./support/fixtures";

const POSTING = "https://jobs.lever.co/acmerobotics/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f";
const APPLY = `${POSTING}/apply`;

const leverJob = () => parseDocument(fixtureHtml("lever-job.html"));
const leverApply = () => parseDocument(fixtureHtml("lever-apply.html"));

/** Written by hand from test/fixtures/lever-job.html's markup before the reader was run against it. */
const EXPECTED_DESCRIPTION = [
  "About the team",
  "Acme Robotics runs the fleet that moves parcels through our warehouses.",
  "",
  "What you'll do",
  "",
  "- Operate Kubernetes clusters for the robot fleet.",
  "- Automate infrastructure with Terraform.",
  "",
  "What we value",
  "",
  "- 3+ years operating Linux in production.",
  "- Strong Python and Go.",
  "",
  "Acme Robotics is an equal opportunity employer.",
].join("\n");

describe("a Lever posting", () => {
  it("reads every field, exactly", () => {
    expect(extractJob(leverJob(), POSTING)).toEqual({
      status: "extracted",
      site: "Lever",
      warnings: [],
      job: {
        source_type: "lever",
        source_url: POSTING,
        external_job_id: "3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f",
        job_title: "Platform Engineer",
        company_name: "Acme Robotics",
        location_raw: "Berlin, Germany",
        description_text: EXPECTED_DESCRIPTION,
      },
    });
  });

  it("keeps tracking parameters for the API to strip, drops the fragment", () => {
    const result = extractJob(leverJob(), `${POSTING}?lever-source=newsletter#top`);
    expect(result.status === "extracted" && result.job.source_url).toBe(`${POSTING}?lever-source=newsletter`);
  });

  it("falls back to the logo for the company when the title does not say", () => {
    const document = leverJob();
    document.title = "Jobs";
    const result = extractJob(document, POSTING);
    expect(result.status === "extracted" && result.job.company_name).toBe("Acme Robotics");
  });

  it("fails explicitly, naming what is missing, when the layout is not Lever's", () => {
    const document = leverJob();
    document.querySelector(".posting-headline h2")!.remove();
    expect(extractJob(document, POSTING)).toMatchObject({ status: "failed", site: "Lever", missing: ["job_title"] });
  });

  it("reports a board as not a posting", () => {
    expect(extractJob(leverJob(), "https://jobs.lever.co/acmerobotics")).toMatchObject({ status: "not_job_page", site: "Lever" });
  });

  it("reports the application page as such, pointing at its posting — never saved as a posting", () => {
    expect(extractJob(leverApply(), APPLY)).toEqual({
      status: "application_page",
      site: "Lever",
      postingUrl: POSTING,
      message: expect.stringContaining("EasyApply saves a job from its posting page"),
    });
  });
});

function fields(): FieldDescriptor[] {
  const result = detectForm(leverApply(), APPLY);
  if (result.status !== "detected") throw new Error(`no form: ${result.status}`);
  return result.fields;
}

describe("a Lever application form", () => {
  it("reads each question once — a choice group as one field — and none of Lever's hidden inputs", () => {
    expect(fields().map((field) => [field.key, field.control, field.label, field.required])).toEqual([
      ["name:resume", "file", "Resume/CV", true],
      ["name:name", "text", "Full name", true],
      ["name:email", "email", "Email", true],
      ["name:phone", "text", "Phone", false],
      ["name:location", "text", "Current location", true],
      ["name:org", "text", "Current company", false],
      ["name:urls[LinkedIn]", "text", "LinkedIn URL", false],
      ["name:urls[GitHub]", "text", "GitHub URL", false],
      ["name:urls[Portfolio]", "text", "Portfolio URL", false],
      ["name:cards[11111111-aaaa-4bbb-8ccc-000000000001][field0]", "checkbox_group", "Language skills (check all that apply)", true],
      ["name:cards[11111111-aaaa-4bbb-8ccc-000000000001][field1]", "text", "Preferred name | What would you like us to call you?", false],
      ["name:cards[22222222-aaaa-4bbb-8ccc-000000000002][field0]", "radio_group", "Are you legally authorized to work in the country for which you are applying?", true],
      ["name:cards[22222222-aaaa-4bbb-8ccc-000000000002][field1]", "radio_group", "Will you now or in the future require sponsorship for employment visa status?", true],
      ["name:cards[33333333-aaaa-4bbb-8ccc-000000000003][field0]", "select", "Which university did you last attend?", true],
      ["name:cards[33333333-aaaa-4bbb-8ccc-000000000003][field1]", "textarea", "What has been your proudest project, and why?", false],
      ["name:comments", "textarea", "Add a cover letter or anything else you want to share.", false],
      ["name:consent[marketing]", "checkbox", "I agree to be contacted about future opportunities", false],
    ]);
  });

  it("counts only text controls as settable", () => {
    const settable = fields().filter((field) => field.settable).map((field) => field.key);
    expect(settable).toEqual([
      "name:name",
      "name:email",
      "name:phone",
      "name:location",
      "name:org",
      "name:urls[LinkedIn]",
      "name:urls[GitHub]",
      "name:urls[Portfolio]",
      "name:cards[11111111-aaaa-4bbb-8ccc-000000000001][field1]",
      "name:cards[33333333-aaaa-4bbb-8ccc-000000000003][field1]",
      "name:comments",
    ]);
  });

  it("marks Lever's equal-opportunity questions as demographic", () => {
    const document = leverApply();
    const select = document.createElement("select");
    select.name = "eeo[gender]";
    document.querySelector("#application-form ul")!.append(select);
    const gender = detectForm(document, APPLY);
    expect(gender.status === "detected" && gender.fields.find((field) => field.key === "name:eeo[gender]")?.section).toBe("demographic");
  });

  it("is on the application page only; the posting page says where the form is", () => {
    expect(detectForm(leverJob(), POSTING)).toMatchObject({ status: "no_form", message: expect.stringContaining("Press Apply") });
  });

  it("resolves a key only to the form's own, non-hidden controls", () => {
    const document = leverApply();
    expect(leverApplication.resolve(document, "name:email")).toBe(document.querySelector('[name="email"]'));
    expect(leverApplication.resolve(document, "name:accountId")).toBeNull();
    expect(leverApplication.resolve(document, "name:h-captcha-response")).toBeNull();
    const outside = document.createElement("input");
    outside.name = "search";
    document.body.append(outside);
    expect(leverApplication.resolve(document, "name:search")).toBeNull();
    expect(leverApplication.resolve(document, "id:email")).toBeNull();
  });

  it("gets proposals from the same mapping as Greenhouse, with the same refusals", () => {
    const table = Object.fromEntries(
      proposeAll(fields(), SNAPSHOT).map((proposal) => [
        proposal.field.label,
        [proposal.action, proposal.confidence, proposal.value, proposal.classification.sensitive],
      ]),
    );
    expect(table).toEqual({
      "Resume/CV": ["attach", "none", null, null],
      "Full name": ["fill", "exact", "Ada Lovelace", null],
      Email: ["fill", "exact", "ada@example.test", null],
      Phone: ["fill", "exact", "+44 20 7946 0000", null],
      "Current location": ["fill", "exact", "London, United Kingdom", null],
      "Current company": ["review", "none", null, null],
      "LinkedIn URL": ["fill", "exact", "https://www.linkedin.com/in/ada-lovelace", null],
      "GitHub URL": ["fill", "exact", "https://github.com/ada", null],
      "Portfolio URL": ["fill", "exact", "https://ada.example.test", null],
      "Language skills (check all that apply)": ["review", "none", null, null],
      "Preferred name | What would you like us to call you?": ["review", "none", null, null],
      "Are you legally authorized to work in the country for which you are applying?": ["review", "none", null, "work_authorization"],
      "Will you now or in the future require sponsorship for employment visa status?": ["review", "none", null, "sponsorship"],
      "Which university did you last attend?": ["review", "none", null, null],
      "What has been your proudest project, and why?": ["review", "none", null, null],
      "Add a cover letter or anything else you want to share.": ["review", "none", null, null],
      "I agree to be contacted about future opportunities": ["review", "none", null, "legal_attestation"],
    });
  });

  it("fills approved text fields and never sets a radio, a checkbox or a select", () => {
    const document = leverApply();
    const events: string[] = [];
    document.getElementById("application-form")!.addEventListener("submit", () => events.push("submit"));
    document.getElementById("btn-submit")!.addEventListener("click", () => events.push("click"));

    const report = fillForm(document, APPLY, [
      { key: "name:name", value: "Ada Lovelace", label: "Full name" },
      { key: "name:urls[LinkedIn]", value: "https://www.linkedin.com/in/ada-lovelace", label: "LinkedIn URL" },
      { key: "name:cards[22222222-aaaa-4bbb-8ccc-000000000002][field0]", value: "Yes", label: "Are you legally authorized to work in the country for which you are applying?" },
      { key: "name:cards[11111111-aaaa-4bbb-8ccc-000000000001][field0]", value: "English", label: "Language skills (check all that apply)" },
      { key: "name:cards[33333333-aaaa-4bbb-8ccc-000000000003][field0]", value: "University A", label: "Which university did you last attend?" },
      { key: "name:consent[marketing]", value: "1", label: "I agree to be contacted about future opportunities" },
    ]);

    expect(report.filled).toEqual(["name:name", "name:urls[LinkedIn]"]);
    expect(report.skipped.map((skip) => skip.reason)).toEqual(["sensitive", "not_settable", "not_settable", "sensitive"]);
    expect((document.querySelector('[name="name"]') as HTMLInputElement).value).toBe("Ada Lovelace");
    expect([...document.querySelectorAll<HTMLInputElement>('input[type="radio"], input[type="checkbox"]')].some((box) => box.checked)).toBe(false);
    expect((document.querySelector("select") as HTMLSelectElement).value).toBe("");
    expect(events).toEqual([]);
  });

  it("answers the content script's detect message on the application page", () => {
    const answer = answerContentMessage({ type: DETECT_FORM }, leverApply(), APPLY);
    expect(answer).toMatchObject({ status: "detected", site: "Lever" });
  });
});
