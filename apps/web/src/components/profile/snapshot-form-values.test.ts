import { describe, expect, it } from "vitest";

import { filledSnapshot } from "@/test/fixtures";

import {
  EMPTY_CONTACT,
  EMPTY_EXPERIENCE,
  EMPTY_LINK,
  EMPTY_SKILL,
  projectLinksFromText,
  projectLinksProblem,
  projectLinksToText,
  toFormValues,
  toSnapshotInput,
  type SnapshotFormValues,
} from "./snapshot-form-values";

const emptyForm: SnapshotFormValues = {
  contact: EMPTY_CONTACT,
  summary: "",
  links: [],
  experiences: [],
  education: [],
  skills: [],
  projects: [],
  certifications: [],
};

describe("toSnapshotInput", () => {
  it("sends null rather than empty strings for optional fields", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      skills: [{ ...EMPTY_SKILL, name: "Python" }],
    });

    expect(result.skills?.[0]).toEqual({
      name: "Python",
      category: null,
      proficiency: null,
      years_of_experience: null,
    });
  });

  it("drops the precision when the date is empty, because the two travel together", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      experiences: [
        { ...EMPTY_EXPERIENCE, company: "X", job_title: "Y", start_date: "2026-05-15" },
      ],
    });

    const experience = result.experiences?.[0];
    expect(experience?.start_date).toBe("2026-05-15");
    expect(experience?.start_date_precision).toBe("day");
    // No end date was given, so no end precision may be sent either.
    expect(experience?.end_date).toBeNull();
    expect(experience?.end_date_precision).toBeNull();
  });

  it("keeps a precision the user actually chose", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      experiences: [
        {
          ...EMPTY_EXPERIENCE,
          company: "X",
          job_title: "Y",
          start_date: "2026-05-01",
          start_date_precision: "month",
        },
      ],
    });

    expect(result.experiences?.[0]?.start_date_precision).toBe("month");
  });

  it("splits technologies and discards blanks", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      projects: [
        {
          name: "EasyApply",
          role: "",
          description: "",
          start_date: "",
          start_date_precision: "day",
          end_date: "",
          end_date_precision: "day",
          project_url: "",
          technologies: "FastAPI, , PostgreSQL ,",
          links: "",
        },
      ],
    });

    expect(result.projects?.[0]?.technologies).toEqual(["FastAPI", "PostgreSQL"]);
  });

  it("converts years of experience to a number, keeping half years", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      skills: [{ ...EMPTY_SKILL, name: "Django", years_of_experience: "1.5" }],
    });

    expect(result.skills?.[0]?.years_of_experience).toBe(1.5);
  });

  it("trims whitespace off required text", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      skills: [{ ...EMPTY_SKILL, name: "  Rust  " }],
    });

    expect(result.skills?.[0]?.name).toBe("Rust");
  });
});

describe("contact, summary and links", () => {
  it("sends a contact record with nothing in it as absent, not as blanks", () => {
    // Four nulls would overwrite whatever the resume read with an empty record,
    // and would render a contact block with nothing in it.
    expect(toSnapshotInput(emptyForm).contact).toBeNull();
    expect(toSnapshotInput(emptyForm).summary).toBeNull();
  });

  it("keeps a contact record where any one field is filled", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      contact: { ...EMPTY_CONTACT, full_name: "  K Subhash  " },
    });

    expect(result.contact).toEqual({
      full_name: "K Subhash",
      email: null,
      phone: null,
      location: null,
    });
  });

  it("drops a link row the user added and left blank", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      links: [
        { url: "https://github.com/example", label: "GitHub" },
        { ...EMPTY_LINK },
      ],
    });

    expect(result.links).toEqual([
      { url: "https://github.com/example", label: "GitHub" },
    ]);
  });

  it("keeps a link that has no label, because the URL is the claim", () => {
    const result = toSnapshotInput({
      ...emptyForm,
      links: [{ url: "https://example.com", label: "" }],
    });

    expect(result.links).toEqual([{ url: "https://example.com", label: null }]);
  });
});

describe("round trip", () => {
  it("survives a full snapshot unchanged", () => {
    const result = toSnapshotInput(toFormValues(filledSnapshot));

    expect(result.contact).toEqual(filledSnapshot.contact);
    expect(result.summary).toEqual(filledSnapshot.summary);
    expect(result.links).toEqual(filledSnapshot.links);
    expect(result.skills).toEqual(filledSnapshot.skills);
    expect(result.experiences).toEqual(filledSnapshot.experiences);
    expect(result.education).toEqual(filledSnapshot.education);
    expect(result.projects).toEqual(filledSnapshot.projects);
    expect(result.certifications).toEqual(filledSnapshot.certifications);
  });
});

describe("project links", () => {
  // A save REPLACES the snapshot, so a form that did not carry a project's
  // links would erase every one the resume reader found.
  it("survive a round trip through the form unchanged", () => {
    const values = toFormValues(filledSnapshot);
    const input = toSnapshotInput(values);
    expect(input.projects?.[0]?.links).toEqual([
      { url: "https://github.com/example/easyapply", label: "Repository" },
    ]);
  });

  it("read a label with spaces, an address alone, and skip blank lines", () => {
    expect(
      projectLinksFromText("Live demo https://demo.example.com\n\nhttps://github.com/x/y\n"),
    ).toEqual([
      { url: "https://demo.example.com", label: "Live demo" },
      { url: "https://github.com/x/y", label: null },
    ]);
  });

  it("split at a pasted tab and keep Windows line endings out of the address", () => {
    expect(projectLinksFromText("GitHub\thttps://x.example/y\r\nDemo  https://d.example\r\n")).toEqual([
      { url: "https://x.example/y", label: "GitHub" },
      { url: "https://d.example", label: "Demo" },
    ]);
  });

  it("accept the address forms resume extraction stores", () => {
    for (const address of [
      "https://github.com/x",
      "mailto:me@example.com",
      "www.example.com",
      "github.com/x/y",
      "example.dev",
    ]) {
      expect(projectLinksProblem(`Site ${address}`), address).toBeUndefined();
    }
    expect(projectLinksProblem("")).toBeUndefined();
  });

  it("refuse a line that does not end with an address, naming the line", () => {
    // Each of these used to save with the label, or the whole line, as the URL.
    expect(projectLinksProblem("GitHub")).toMatch(/^This line does not end with a web address/);
    expect(projectLinksProblem("https://x.example GitHub")).toMatch(/^This line/);
    expect(projectLinksProblem("Demo https://d.example\nRepository")).toMatch(/^Line 2 /);
  });

  it("print one link per line, label first", () => {
    expect(
      projectLinksToText([
        { url: "https://demo.example.com", label: "Demo" },
        { url: "https://github.com/x/y", label: null },
      ]),
    ).toBe("Demo https://demo.example.com\nhttps://github.com/x/y");
  });
});
