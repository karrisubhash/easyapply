import type { Snapshot } from "@easyapply/shared";

import { detectForm } from "../src/forms/registry";
import type { FieldDescriptor } from "../src/forms/types";
import { propose, proposeAll, type Proposal } from "../src/profile/mapping";
import { SNAPSHOT } from "./support/backend-fixtures";
import { JOB_URL, fixtureHtml, parseDocument } from "./support/fixtures";

function fixtureFields(): FieldDescriptor[] {
  const result = detectForm(parseDocument(fixtureHtml("greenhouse-application.html")), JOB_URL);
  if (result.status !== "detected") throw new Error("no form");
  return result.fields;
}

function table(proposals: Proposal[]) {
  return Object.fromEntries(
    proposals.map((proposal) => [
      proposal.field.key.slice(3),
      [proposal.action, proposal.confidence, proposal.value, proposal.preselected, proposal.classification.sensitive],
    ]),
  );
}

const TEXT: FieldDescriptor = {
  key: "id:q",
  control: "text",
  label: "",
  required: false,
  section: "questions",
  autocomplete: null,
  filled: false,
  settable: true,
  maxLength: 255,
};

function withContact(contact: Partial<NonNullable<Snapshot["contact"]>>): Snapshot {
  return { ...SNAPSHOT, contact: { full_name: null, email: null, phone: null, location: null, ...contact } };
}

describe("proposals for the Greenhouse fixture form, from the canonical profile", () => {
  it("fills what the profile states, adapts only what is safe, and leaves every question it cannot answer", () => {
    expect(table(proposeAll(fixtureFields(), SNAPSHOT))).toEqual({
      first_name: ["fill", "derived", "Ada", true, null],
      last_name: ["fill", "derived", "Lovelace", true, null],
      preferred_name: ["review", "none", null, false, null],
      email: ["fill", "exact", "ada@example.test", true, null],
      country: ["review", "none", null, false, null],
      phone: ["fill", "exact", "+44 20 7946 0000", true, null],
      "candidate-location": ["review", "none", null, false, null],
      resume: ["attach", "none", null, false, null],
      cover_letter: ["attach", "none", null, false, null],
      question_61000001: ["fill", "exact", "https://www.linkedin.com/in/ada-lovelace", true, null],
      question_61000002: ["fill", "exact", "https://ada.example.test", true, null],
      question_61000003: ["fill", "exact", "https://github.com/ada", true, null],
      question_61000004: ["review", "none", null, false, null],
      question_61000005: ["review", "none", null, false, null],
      question_61000006: ["review", "none", null, false, "work_authorization"],
      question_61000007: ["review", "none", null, false, "sponsorship"],
      question_61000008: ["review", "none", null, false, "location_eligibility"],
      question_61000009: ["review", "none", null, false, "relocation"],
      question_61000010: ["review", "none", null, false, "salary"],
      question_61000011: ["review", "none", null, false, "years_of_experience"],
      question_61000012: ["review", "none", null, false, "notice_period"],
      question_61000013: ["review", "none", null, false, "disability"],
      question_61000014: ["review", "none", null, false, "employment_history"],
      question_61000015: ["review", "none", null, false, null],
      gender: ["review", "none", null, false, "demographic"],
      hispanic_ethnicity: ["review", "none", null, false, "demographic"],
      veteran_status: ["review", "none", null, false, "demographic"],
      disability_status: ["review", "none", null, false, "demographic"],
    });
  });

  it("never proposes a value for a sensitive question, even a plain text box", () => {
    const proposals = proposeAll(fixtureFields(), SNAPSHOT);
    for (const proposal of proposals.filter((candidate) => candidate.classification.sensitive)) {
      expect(proposal.value).toBeNull();
      expect(proposal.action).toBe("review");
      expect(proposal.note).toContain("EasyApply never answers that for you");
    }
    // The salary and years questions are text inputs the filler COULD set; they are still left.
    const salary = proposals.find((proposal) => proposal.field.key === "id:question_61000010")!;
    expect(salary.field.settable).toBe(true);
    expect(salary.value).toBeNull();
  });

  it("offers the profile's location as a hint for a location picker, and never fills the picker", () => {
    const location = proposeAll(fixtureFields(), SNAPSHOT).find((proposal) => proposal.field.key === "id:candidate-location")!;
    expect(location.action).toBe("review");
    expect(location.hint).toBe("London, United Kingdom");
    expect(location.note).toContain("Choose this yourself");
  });

  it("gives every field a reason the person can read", () => {
    for (const proposal of proposeAll(fixtureFields(), SNAPSHOT)) expect(proposal.note.length).toBeGreaterThan(10);
  });
});

describe("names", () => {
  it.each([
    ["Ada Lovelace", "Ada", "Lovelace", "derived"],
    ["Mary Ann Smith", null, null, "none"],
    ["Juan Carlos de la Vega", null, null, "none"],
    ["Madonna", null, null, "none"],
  ])("%j → first %j, last %j", (full_name, first, last, confidence) => {
    const snapshot = withContact({ full_name });
    const firstProposal = propose({ ...TEXT, label: "First Name", autocomplete: "given-name" }, snapshot);
    const lastProposal = propose({ ...TEXT, label: "Last Name", autocomplete: "family-name" }, snapshot);
    expect([firstProposal.value, lastProposal.value]).toEqual([first, last]);
    expect(firstProposal.confidence).toBe(confidence);
    if (first === null) expect(firstProposal.note).toContain("isn't something EasyApply can know");
  });

  it("a full-name field gets the whole name, exactly", () => {
    expect(propose({ ...TEXT, label: "Full name" }, withContact({ full_name: "Mary Ann Smith" }))).toMatchObject({
      action: "fill",
      confidence: "exact",
      value: "Mary Ann Smith",
      preselected: true,
    });
  });
});

describe("links", () => {
  const linkedin = { ...TEXT, label: "LinkedIn Profile" };
  const github = { ...TEXT, label: "GitHub URL" };
  const site = { ...TEXT, label: "Website" };

  it("uses only the person's own links, never a project's", () => {
    const snapshot: Snapshot = {
      ...SNAPSHOT,
      links: [],
      projects: [
        {
          name: "Engine",
          description: null,
          role: null,
          start_date: null,
          end_date: null,
          project_url: "https://github.com/ada/engine",
          technologies: [],
          links: [{ url: "https://github.com/ada/engine", label: "Code" }],
        },
      ],
    };
    expect(propose(github, snapshot)).toMatchObject({ action: "review", value: null });
    expect(propose(site, snapshot)).toMatchObject({ action: "review", value: null });
  });

  it("refuses to choose between two LinkedIn links", () => {
    const snapshot: Snapshot = {
      ...SNAPSHOT,
      links: [
        { url: "https://linkedin.com/in/ada", label: null },
        { url: "https://uk.linkedin.com/in/ada-l", label: null },
      ],
    };
    const proposal = propose(linkedin, snapshot);
    expect(proposal.value).toBeNull();
    expect(proposal.note).toContain("2 LinkedIn links");
  });

  it("is not fooled by a look-alike host", () => {
    const snapshot: Snapshot = { ...SNAPSHOT, links: [{ url: "https://linkedin.com.evil.example/in/ada", label: "LinkedIn" }] };
    expect(propose(linkedin, snapshot).value).toBeNull();
  });

  it("adapts, and does not tick, a GitHub link to a repository", () => {
    const snapshot: Snapshot = { ...SNAPSHOT, links: [{ url: "https://github.com/ada/engine", label: null }] };
    expect(propose(github, snapshot)).toMatchObject({ action: "fill", confidence: "derived", preselected: false });
  });

  it("picks a website only when it is the one other link, or the one labelled as a website", () => {
    const unlabelled: Snapshot = { ...SNAPSHOT, links: [{ url: "https://ada.example.test", label: null }] };
    expect(propose(site, unlabelled)).toMatchObject({ confidence: "derived", preselected: false });
    const two: Snapshot = {
      ...SNAPSHOT,
      links: [
        { url: "https://a.example.test", label: null },
        { url: "https://b.example.test", label: null },
      ],
    };
    expect(propose(site, two).value).toBeNull();
  });
});

describe("what is never proposed", () => {
  it("a field the profile has nothing for says so", () => {
    expect(propose({ ...TEXT, label: "Email" }, withContact({}))).toMatchObject({
      action: "review",
      value: null,
      note: "Your profile doesn't have an email address.",
    });
  });

  it("an unknown question stays unknown", () => {
    expect(propose({ ...TEXT, label: "How did you hear about this job?" }, SNAPSHOT)).toMatchObject({
      action: "review",
      value: null,
      note: expect.stringContaining("requires your review"),
    });
  });

  it("a free-text question is the person's to write", () => {
    expect(propose({ ...TEXT, control: "textarea", label: "Why do you want to work here?" }, SNAPSHOT)).toMatchObject({
      action: "review",
      value: null,
    });
  });

  it("a field that already holds something is left as it is", () => {
    expect(propose({ ...TEXT, label: "Email", filled: true }, SNAPSHOT)).toMatchObject({ action: "leave", value: null });
  });

  it("a value longer than the field allows is not cut to fit", () => {
    const proposal = propose({ ...TEXT, label: "Email", maxLength: 5 }, SNAPSHOT);
    expect(proposal).toMatchObject({ action: "review", value: null, hint: "ada@example.test" });
  });

  it("skills are only ever a suggestion", () => {
    expect(propose({ ...TEXT, label: "Skills" }, SNAPSHOT)).toMatchObject({
      action: "fill",
      confidence: "derived",
      value: "Python, SQL",
      preselected: false,
    });
  });
});
