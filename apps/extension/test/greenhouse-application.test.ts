import { greenhouseApplication } from "../src/adapters/greenhouse/application";
import { detectForm } from "../src/forms/registry";
import type { FieldDescriptor, FormDetection } from "../src/forms/types";
import { BOARD_URL, JOB_URL, fixtureHtml, jobDocument, parseDocument } from "./support/fixtures";

export function applicationDocument(): Document {
  return parseDocument(fixtureHtml("greenhouse-application.html"));
}

function detected(document: Document, href = JOB_URL): FieldDescriptor[] {
  const result: FormDetection = detectForm(document, href);
  if (result.status !== "detected") throw new Error(`expected a form, got ${result.status}`);
  return result.fields;
}

function byKey(fields: FieldDescriptor[], id: string): FieldDescriptor {
  const found = fields.find((candidate) => candidate.key === `id:${id}`);
  if (!found) throw new Error(`no field ${id}`);
  return found;
}

describe("the Greenhouse application form", () => {
  it("reads every question on the form, and nothing that is not one", () => {
    const fields = detected(applicationDocument());
    expect(fields.map((field) => field.key.slice(3))).toEqual([
      "first_name",
      "last_name",
      "preferred_name",
      "email",
      "country",
      "phone",
      "candidate-location",
      "resume",
      "cover_letter",
      ...Array.from({ length: 15 }, (_, index) => `question_${61000001 + index}`),
      "gender",
      "hispanic_ethnicity",
      "veteran_status",
      "disability_status",
    ]);
  });

  it("reads each field's kind, label, requirement and section as the page states them", () => {
    const fields = detected(applicationDocument());
    expect(byKey(fields, "first_name")).toEqual({
      key: "id:first_name",
      control: "text",
      label: "First Name",
      required: true,
      section: "core",
      autocomplete: "given-name",
      filled: false,
      settable: true,
      maxLength: 255,
    });
    expect(byKey(fields, "phone")).toMatchObject({ control: "tel", label: "Phone", required: false, autocomplete: "tel", settable: true });
    expect(byKey(fields, "email")).toMatchObject({ control: "text", autocomplete: "email", required: true });
    expect(byKey(fields, "question_61000005")).toMatchObject({
      control: "textarea",
      label: "Why do you want to work at Acme Robotics?",
      required: true,
      section: "questions",
      settable: true,
    });
    expect(byKey(fields, "question_61000004").label).toBe("What's the name you'd prefer us to use throughout the interview process?");
  });

  it("reports react-select choice questions as comboboxes it cannot set", () => {
    const fields = detected(applicationDocument());
    for (const id of ["country", "candidate-location", "question_61000006", "question_61000007", "gender"]) {
      expect(byKey(fields, id)).toMatchObject({ control: "combobox", settable: false });
    }
    expect(byKey(fields, "question_61000006")).toMatchObject({
      label: "Are you legally authorized to work in the country where this role is based?",
      required: true,
    });
  });

  it("names a file input by its upload block, not by its 'Attach' label", () => {
    const fields = detected(applicationDocument());
    expect(byKey(fields, "resume")).toMatchObject({ control: "file", label: "Resume/CV", required: true, settable: false });
    expect(byKey(fields, "cover_letter")).toMatchObject({ control: "file", label: "Cover Letter", required: false });
  });

  it("puts the self-identification survey in the demographic section", () => {
    const fields = detected(applicationDocument());
    for (const id of ["gender", "hispanic_ethnicity", "veteran_status", "disability_status"]) {
      expect(byKey(fields, id).section).toBe("demographic");
    }
    expect(fields.filter((field) => field.section === "demographic")).toHaveLength(4);
  });

  it("says whether a field holds something, never what", () => {
    const document = applicationDocument();
    const email = document.getElementById("email") as HTMLInputElement;
    email.value = "typed-by-the-person@example.test";
    const fields = detected(document);
    expect(byKey(fields, "email").filled).toBe(true);
    expect(JSON.stringify(fields)).not.toContain("typed-by-the-person");
  });

  it("does not count a hidden, disabled or read-only field as settable", () => {
    const document = applicationDocument();
    (document.getElementById("first_name")!.closest(".field-wrapper") as HTMLElement).style.display = "none";
    (document.getElementById("last_name") as HTMLInputElement).disabled = true;
    (document.getElementById("preferred_name") as HTMLInputElement).readOnly = true;
    document.getElementById("email")!.closest(".field-wrapper")!.setAttribute("aria-hidden", "true");
    const fields = detected(document);
    for (const id of ["first_name", "last_name", "preferred_name", "email"]) expect(byKey(fields, id).settable).toBe(false);
    expect(byKey(fields, "phone").settable).toBe(true);
  });

  it("finds no form on a board, or on a posting page without one", () => {
    expect(detectForm(parseDocument(fixtureHtml("greenhouse-board.html")), BOARD_URL)).toMatchObject({ status: "no_form" });
    const bare = jobDocument();
    bare.getElementById("application-form")!.remove();
    expect(detectForm(bare, JOB_URL)).toMatchObject({ status: "no_form", site: "Greenhouse" });
  });

  it("is not asked about other sites", () => {
    expect(detectForm(applicationDocument(), "https://www.linkedin.com/jobs/view/123")).toMatchObject({ status: "unsupported" });
    expect(detectForm(applicationDocument(), "not a url")).toMatchObject({ status: "unsupported" });
  });

  it("resolves a key only to a control inside the application form", () => {
    const document = applicationDocument();
    const outside = document.createElement("input");
    outside.id = "sneaky";
    document.body.append(outside);
    expect(greenhouseApplication.resolve(document, "id:first_name")).toBe(document.getElementById("first_name"));
    expect(greenhouseApplication.resolve(document, "id:sneaky")).toBeNull();
    expect(greenhouseApplication.resolve(document, "id:missing")).toBeNull();
    expect(greenhouseApplication.resolve(document, "first_name")).toBeNull();
  });

  it("only reads: the page is byte-identical afterwards", () => {
    const document = applicationDocument();
    const before = document.documentElement.outerHTML;
    detected(document);
    expect(document.documentElement.outerHTML).toBe(before);
  });
});
