import { answerContentMessage } from "../src/content/handler";
import { fillForm } from "../src/forms/fill";
import { FILL_FORM, FILL_LIMITS } from "../src/shared/messages";
import { JOB_URL, fixtureHtml, parseDocument } from "./support/fixtures";

function page() {
  const document = parseDocument(fixtureHtml("greenhouse-application.html"));
  const form = document.getElementById("application-form") as HTMLFormElement;
  const events: string[] = [];
  form.addEventListener("submit", () => events.push("submit"));
  for (const button of form.querySelectorAll("button")) button.addEventListener("click", () => events.push("click"));
  return { document, form, events };
}

const input = (document: Document, id: string) => document.getElementById(id) as HTMLInputElement;

describe("filling approved fields", () => {
  it("sets each approved, empty text field and tells the page it changed", () => {
    const { document, events } = page();
    const seen: string[] = [];
    input(document, "email").addEventListener("input", () => seen.push("input"));
    input(document, "email").addEventListener("change", () => seen.push("change"));

    const report = fillForm(document, JOB_URL, [
      { key: "id:email", value: "ada@example.test", label: "Email" },
      { key: "id:first_name", value: "Ada", label: "First Name" },
      { key: "id:question_61000001", value: "https://www.linkedin.com/in/ada-lovelace", label: "LinkedIn Profile" },
    ]);

    expect(report).toEqual({ filled: ["id:email", "id:first_name", "id:question_61000001"], skipped: [] });
    expect(input(document, "email").value).toBe("ada@example.test");
    expect(input(document, "first_name").value).toBe("Ada");
    expect(seen).toEqual(["input", "change"]);
    // Nothing was submitted or clicked.
    expect(events).toEqual([]);
  });

  it("never overwrites what the person typed", () => {
    const { document } = page();
    input(document, "email").value = "typed@example.test";
    expect(fillForm(document, JOB_URL, [{ key: "id:email", value: "ada@example.test", label: "Email" }])).toEqual({
      filled: [],
      skipped: [{ key: "id:email", reason: "has_value" }],
    });
    expect(input(document, "email").value).toBe("typed@example.test");
  });

  it("refuses a sensitive question even when told to fill it", () => {
    const { document } = page();
    const report = fillForm(document, JOB_URL, [
      { key: "id:question_61000010", value: "200000", label: "What are your salary expectations for this role?" },
      { key: "id:question_61000011", value: "12", label: "How many years of professional experience do you have with Python?" },
    ]);
    expect(report.skipped.map((skip) => skip.reason)).toEqual(["sensitive", "sensitive"]);
    expect(input(document, "question_61000010").value).toBe("");
  });

  it("does not set choices or files", () => {
    const { document } = page();
    const report = fillForm(document, JOB_URL, [
      { key: "id:question_61000006", value: "Yes", label: "Are you legally authorized to work in the country where this role is based?" },
      { key: "id:candidate-location", value: "London", label: "Location (City)" },
      { key: "id:resume", value: "C:\\resume.pdf", label: "Resume/CV" },
    ]);
    expect(report.filled).toEqual([]);
    expect(report.skipped.map((skip) => skip.reason)).toEqual(["sensitive", "not_settable", "not_settable"]);
    expect(input(document, "candidate-location").value).toBe("");
  });

  it("leaves a field whose label changed after it was approved", () => {
    const { document } = page();
    document.getElementById("email-label")!.firstChild!.textContent = "Email of a reference";
    expect(fillForm(document, JOB_URL, [{ key: "id:email", value: "ada@example.test", label: "Email" }]).skipped).toEqual([
      { key: "id:email", reason: "changed" },
    ]);
    expect(input(document, "email").value).toBe("");
  });

  it("leaves a field that became a sensitive question, even with the approved label", () => {
    const { document } = page();
    // The page rewrote the label AND the instruction repeats it — the classifier still refuses.
    document.getElementById("email-label")!.firstChild!.textContent = "Email your visa sponsor";
    expect(fillForm(document, JOB_URL, [{ key: "id:email", value: "x", label: "Email your visa sponsor" }]).skipped).toEqual([
      { key: "id:email", reason: "sensitive" },
    ]);
  });

  it("cannot reach anything outside the application form", () => {
    const { document } = page();
    const outside = document.createElement("input");
    outside.id = "search";
    document.body.append(outside);
    expect(fillForm(document, JOB_URL, [{ key: "id:search", value: "x", label: "" }]).skipped).toEqual([{ key: "id:search", reason: "gone" }]);
    expect(outside.value).toBe("");
  });

  it("does not fill a field that disappeared, is hidden, or is too short for the value", () => {
    const { document } = page();
    input(document, "phone").remove();
    (document.getElementById("first_name")!.closest(".field-wrapper") as HTMLElement).style.display = "none";
    input(document, "last_name").maxLength = 3;
    const report = fillForm(document, JOB_URL, [
      { key: "id:phone", value: "1", label: "Phone" },
      { key: "id:first_name", value: "Ada", label: "First Name" },
      { key: "id:last_name", value: "Lovelace", label: "Last Name" },
    ]);
    expect(report.skipped.map((skip) => skip.reason)).toEqual(["gone", "not_settable", "too_long"]);
    expect(input(document, "last_name").value).toBe("");
  });

  it("fills a field once, however often it is listed", () => {
    const { document } = page();
    const report = fillForm(document, JOB_URL, [
      { key: "id:email", value: "first@example.test", label: "Email" },
      { key: "id:email", value: "second@example.test", label: "Email" },
    ]);
    expect(report.filled).toEqual(["id:email"]);
    expect(input(document, "email").value).toBe("first@example.test");
  });

  it("never submits: no submit event, no button click, the form's own submit untouched", () => {
    const { document, form, events } = page();
    const requestSubmit = vi.fn();
    const submit = vi.fn();
    form.requestSubmit = requestSubmit;
    form.submit = submit;
    fillForm(document, JOB_URL, [
      { key: "id:email", value: "ada@example.test", label: "Email" },
      { key: "id:question_61000005", value: "Because.", label: "Why do you want to work at Acme Robotics?" },
    ]);
    expect(events).toEqual([]);
    expect(requestSubmit).not.toHaveBeenCalled();
    expect(submit).not.toHaveBeenCalled();
  });
});

describe("the content script's fill message", () => {
  it("is refused when malformed or oversized, and nothing is filled", () => {
    const { document } = page();
    const tooMany = Array.from({ length: FILL_LIMITS.fields + 1 }, () => ({ key: "id:email", value: "x", label: "Email" }));
    for (const message of [
      { type: FILL_FORM },
      { type: FILL_FORM, instructions: "id:email" },
      { type: FILL_FORM, instructions: [{ key: "id:email", value: 7, label: "Email" }] },
      { type: FILL_FORM, instructions: [{ key: "id:email", value: "x".repeat(FILL_LIMITS.value + 1), label: "Email" }] },
      { type: FILL_FORM, instructions: tooMany },
      { type: "easyapply/submit-application" },
    ]) {
      expect(answerContentMessage(message, document, JOB_URL)).toBeUndefined();
    }
    expect(input(document, "email").value).toBe("");
  });

  it("answers a well-formed one with what it did", () => {
    const { document } = page();
    expect(
      answerContentMessage({ type: FILL_FORM, instructions: [{ key: "id:email", value: "ada@example.test", label: "Email" }] }, document, JOB_URL),
    ).toEqual({ filled: ["id:email"], skipped: [] });
  });
});
