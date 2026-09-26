import { classifyField, normaliseLabel, sensitiveCategory } from "../src/forms/classify";
import type { ControlKind, FormSection } from "../src/forms/types";

function field(label: string, control: ControlKind = "text", extra: { section?: FormSection; autocomplete?: string | null; key?: string } = {}) {
  return { label, control, section: extra.section ?? "questions", autocomplete: extra.autocomplete ?? null, key: extra.key ?? "id:q" };
}

describe("sensitive questions", () => {
  // The wording of real Greenhouse application questions (company names replaced).
  it.each([
    ["Are you legally authorized to work in the United States for our Company?", "work_authorization"],
    ["Are you legally authorized to work in the country where this role is based?", "work_authorization"],
    ["Do you have the right to work in the UK?", "work_authorization"],
    ["Will you now or in the future require sponsorship for a visa to remain in your current location?", "sponsorship"],
    ["Will you require visa sponsorship?", "sponsorship"],
    ["What is your current immigration status?", "visa"],
    ["Citizenship", "visa"],
    ["Gender", "demographic"],
    ["Race and Ethnicity", "demographic"],
    ["Are you Hispanic/Latino?", "demographic"],
    ["I consider myself a member of the LGBTQ+ community. (optional)", "demographic"],
    ["Gender Identity (optional)", "demographic"],
    ["Pronouns", "demographic"],
    ["Veteran Status", "veteran"],
    ["Disability Status", "disability"],
    [
      "It is important to us to create an accessible and inclusive interview experience. Please let us know if there are any adjustments we can make.",
      "disability",
    ],
    ["Have you ever been convicted of a criminal offense?", "criminal_history"],
    ["Do you hold an active security clearance?", "security_clearance"],
    ["Are you currently based in or willing to relocate to the Bay Area for this position?", "relocation"],
    ["Are you willing to relocate for this position?", "relocation"],
    ["What are your salary expectations for this role?", "salary"],
    ["Desired compensation", "salary"],
    ["Expected CTC", "salary"],
    ["What is your notice period?", "notice_period"],
    ["Earliest start date", "notice_period"],
    ["How many years of professional experience do you have with Python?", "years_of_experience"],
    ["Years of experience", "years_of_experience"],
    ["I certify that the information provided is true and complete.", "legal_attestation"],
    ["I acknowledge the privacy notice", "legal_attestation"],
    [
      "Are you subject to any employment agreements and/or post-employment restrictions with your current employer or a past employer?",
      "legal_attestation",
    ],
    ["Are you at least 18 years of age?", "age"],
    ["Date of birth", "age"],
    ["Have you previously worked at or consulted for Acme?", "employment_history"],
    ["Are you currently located in Bangalore?", "location_eligibility"],
    ["Are you currently located in the US?", "location_eligibility"],
    ["What is your current country of residence?", "location_eligibility"],
    ["Please choose the country in which you are located.", "location_eligibility"],
  ])("%j is %s — never answered for the person", (label, category) => {
    expect(sensitiveCategory({ label, section: "questions" })).toBe(category);
    expect(classifyField(field(label)).sensitive).toBe(category);
  });

  it("wins over any other reading of the label", () => {
    expect(classifyField(field("Email address of your immigration lawyer", "email"))).toEqual({ identity: "unknown", sensitive: "visa" });
    expect(classifyField(field("Phone number of your security clearance sponsor", "tel")).sensitive).not.toBeNull();
  });

  it("everything in the demographic section is sensitive, whatever it says", () => {
    expect(classifyField(field("Name", "text", { section: "demographic" })).sensitive).toBe("demographic");
    expect(classifyField(field("Anything at all", "combobox", { section: "demographic" })).sensitive).toBe("demographic");
  });

  it.each([
    "First Name",
    "Email",
    "Phone",
    "LinkedIn Profile",
    "Website",
    "GitHub URL",
    "Location (City)",
    "Why do you want to work at Acme?",
    "How did you hear about this job?",
    "What's your Discord username?",
    "What's the name you'd prefer us to use throughout the interview process?",
  ])("%j is not sensitive", (label) => {
    expect(sensitiveCategory({ label, section: "questions" })).toBeNull();
  });
});

describe("which profile fact a field wants", () => {
  it.each([
    ["First Name", "text", null, "first_name"],
    ["Last Name", "text", null, "last_name"],
    ["Preferred First Name", "text", null, "preferred_name"],
    ["What's the name you'd prefer us to use throughout the interview process?", "text", null, "preferred_name"],
    ["Full name", "text", null, "full_name"],
    ["Email", "text", null, "email"],
    ["E-mail address", "text", null, "email"],
    ["Phone", "tel", null, "phone"],
    ["Mobile number", "text", null, "phone"],
    ["LinkedIn Profile", "text", null, "linkedin"],
    ["LinkedIn URL", "text", null, "linkedin"],
    ["GitHub URL", "text", null, "github"],
    ["Website", "text", null, "website"],
    ["Portfolio URL", "text", null, "website"],
    ["Location", "text", null, "location"],
    ["Location (City)", "combobox", null, "city"],
    ["Skills", "text", null, "skills"],
    ["Why do you want to work at Acme?", "textarea", null, "free_text"],
    // The site's own autocomplete token, when it states one.
    ["Given", "text", "given-name", "first_name"],
    ["Contact", "text", "email", "email"],
  ] as const)("%j (%s, autocomplete=%s) is %s", (label, control, autocomplete, identity) => {
    expect(classifyField(field(label, control, { autocomplete })).identity).toBe(identity);
  });

  it.each([
    "Email of a professional reference",
    "Reference phone number",
    "Emergency contact name",
    "Twitter",
    "How did you hear about this job?",
    "What's your Discord username?",
    "Country",
    "Name of your current manager",
  ])("%j stays unknown: a label that says more than a fact is not that fact", (label) => {
    expect(classifyField(field(label)).identity).toBe("unknown");
  });

  it("names files by their upload block, not their 'Attach' button", () => {
    expect(classifyField(field("Resume/CV", "file", { key: "id:resume" })).identity).toBe("resume");
    expect(classifyField(field("Cover Letter", "file", { key: "id:cover_letter" })).identity).toBe("cover_letter");
    expect(classifyField(field("Writing sample", "file", { key: "id:question_1" })).identity).toBe("unknown");
  });

  it("reads a label without its star, its (optional) and its punctuation", () => {
    expect(normaliseLabel("  First Name * ")).toBe("first name");
    expect(normaliseLabel("Website (optional)")).toBe("website");
    expect(normaliseLabel("Email:")).toBe("email");
  });
});
