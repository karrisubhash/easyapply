/**
 * What an application-form field is asking for — or that it is asking
 * something EasyApply must never answer for the person.
 *
 * Two questions, asked in this order:
 *
 *   1. Is it SENSITIVE? Work authorization, sponsorship, visas and
 *      citizenship, demographics, veteran and disability status, criminal
 *      history, security clearance, relocation, pay, notice period and start
 *      date, years of experience, legal attestations and consents, age, prior
 *      employment with this company, whether the person lives somewhere.
 *      The profile holds none of these facts — and where it holds something
 *      near one (a location, dated jobs), deriving an answer from it is exactly
 *      the guess the person must make themselves. A sensitive field is never
 *      filled, whatever else it looks like: "Email address of your immigration
 *      lawyer" is not an email field. Anything in a demographic section is
 *      sensitive regardless of its wording.
 *   2. Only then, WHICH profile fact does it want? Matched on the whole label
 *      (anchored), or on the site's own `autocomplete` token, so "Email" is an
 *      email field and "Email of a reference" is not. A field that matches
 *      nothing is `unknown`, and unknown stays unknown.
 *
 * Pure, and used on both sides: by the popup to propose values, and again by
 * the content script at fill time, so a field whose label changed into a
 * sensitive question after it was approved is still refused.
 */

import type { ControlKind, FieldDescriptor } from "./types";

export type FieldIdentity =
  | "first_name"
  | "last_name"
  | "full_name"
  | "preferred_name"
  | "email"
  | "phone"
  | "location"
  | "city"
  | "linkedin"
  | "github"
  | "website"
  | "skills"
  | "resume"
  | "cover_letter"
  | "free_text"
  | "unknown";

export type SensitiveCategory =
  | "work_authorization"
  | "sponsorship"
  | "visa"
  | "demographic"
  | "veteran"
  | "disability"
  | "criminal_history"
  | "security_clearance"
  | "relocation"
  | "salary"
  | "notice_period"
  | "years_of_experience"
  | "legal_attestation"
  | "age"
  | "employment_history"
  | "location_eligibility";

export interface Classification {
  identity: FieldIdentity;
  sensitive: SensitiveCategory | null;
}

/** What each sensitive category is, in the words the review screen uses. */
export const SENSITIVE_LABELS: Record<SensitiveCategory, string> = {
  work_authorization: "work authorization",
  sponsorship: "visa sponsorship",
  visa: "visa or citizenship status",
  demographic: "a demographic question",
  veteran: "veteran status",
  disability: "disability or accommodations",
  criminal_history: "criminal history",
  security_clearance: "security clearance",
  relocation: "willingness to relocate",
  salary: "pay expectations",
  notice_period: "notice period or start date",
  years_of_experience: "years of experience",
  legal_attestation: "a legal statement or consent",
  age: "age",
  employment_history: "previous employment with this company",
  location_eligibility: "where you live or can work",
};

/**
 * Checked in order; the first match wins. Word-bounded, on the lower-cased
 * label. Broad on purpose: a false "sensitive" costs the person typing one
 * answer; a missed one could put a wrong answer to a legal question on their
 * application.
 */
const SENSITIVE: readonly (readonly [SensitiveCategory, RegExp])[] = [
  ["sponsorship", /\bsponsor/],
  ["work_authorization", /\b(authori[sz]ed|authori[sz]ation|eligible|eligibility|entitled|permitted|right) to work\b|\bwork (authori[sz]ation|permit|eligibility|visa)\b|\blegally (able|authori[sz]ed|permitted|eligible)\b/],
  ["visa", /\bvisas?\b|\bimmigration\b|\bcitizen(ship)?\b|\bnationality\b|\bgreen card\b|\bh-?1b\b|\bpermanent resident\b|\bresidency status\b/],
  ["veteran", /\bveterans?\b|\bmilitary\b|\barmed forces\b|\bprotected veteran\b/],
  ["disability", /\bdisabilit(y|ies)\b|\bdisabled\b|\baccommodations?\b|\badjustments?\b|\bneurodiver/],
  ["demographic", /\bgender\b|\bsex\b|\brace\b|\bracial\b|\bethnic(ity)?\b|\bhispanic\b|\blatin[oax]\b|\bsexual orientation\b|\blgbt|\btransgender\b|\bpronouns?\b|\breligio(n|us)\b|\bmarital\b|\bpregnan|\bheritage\b/],
  ["criminal_history", /\bcriminal\b|\bconvict(ed|ion)\b|\bfelon(y|ies)?\b|\barrest(ed)?\b|\bbackground check\b|\bmisdemeanou?r\b/],
  ["security_clearance", /\bclearance\b|\bsecurity vetting\b/],
  ["relocation", /\brelocat(e|ion|ing)\b/],
  ["salary", /\bsalary\b|\bcompensation\b|\bpay\b|\bwages?\b|\bremuneration\b|\bctc\b|\bexpected (base|total|annual)\b|\bdesired (pay|rate|compensation)\b|\bhourly rate\b|\bday rate\b/],
  ["notice_period", /\bnotice period\b|\bnotice\b.*\b(days?|weeks?|months?)\b|\bstart date\b|\bavailable to start\b|\bearliest (start|date|availability)\b|\bwhen (can|could) you start\b|\bavailability\b/],
  ["years_of_experience", /\byears?\b[^.?]*\bexperience\b|\bexperience\b[^.?]*\byears?\b|\bhow many years\b|\bhow long have you\b/],
  ["age", /\bage\b|\bdate of birth\b|\bbirth ?date\b|\bbirthday\b|\b(18|21) years\b|\bover (18|21)\b/],
  ["employment_history", /\bpreviously (worked|been employed|applied|interviewed)\b|\bformer(ly)? (an? )?(employee|employed|contractor)\b|\bworked (at|for|with) [^?]* before\b|\bhave you (ever )?(worked|been employed|consulted)\b|\breferred by an? (current )?employee\b/],
  ["legal_attestation", /\bcertify\b|\battest\b|\backnowledg|\bi (agree|confirm|consent|understand)\b|\bconsent\b|\bterms\b|\bprivacy (policy|notice)\b|\bnon-?compete\b|\bnon-?solicit|\bemployment agreements?\b|\brestrictive covenants?\b|\bpost-employment\b|\bsignature\b|\bsign (here|below)\b|\bdeclare\b|\btrue and (complete|correct)\b/],
  ["location_eligibility", /\bare you (currently )?(located|based|living|residing|resident)\b|\bdo you (currently )?(live|reside)\b|\bcountry (of|in which you are) (residence|located|residing)\b|\bcurrent country\b|\bwilling to (work|commute|travel)\b|\bable to (work|commute) (in|from|on-?site)\b|\btime ?zone\b/],
];

/** The label with the parts that are not its words removed, lower-cased. */
export function normaliseLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/\((optional|required)\)/g, " ")
    .replace(/[*:]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[?.]+$/, "")
    .trim();
}

/** Whole-label forms for each identity. Anchored: a label that says more than this is not this. */
const EXACT_LABELS: readonly (readonly [FieldIdentity, readonly string[]])[] = [
  ["first_name", ["first name", "given name", "first", "forename", "legal first name"]],
  ["last_name", ["last name", "surname", "family name", "last", "legal last name"]],
  ["full_name", ["name", "full name", "your name", "legal name", "full legal name"]],
  ["email", ["email", "e-mail", "email address", "e-mail address", "your email"]],
  ["phone", ["phone", "phone number", "mobile", "mobile number", "mobile phone", "telephone", "telephone number", "cell phone", "contact number"]],
  ["location", ["location", "current location", "your location"]],
  ["city", ["city", "location (city)", "current city", "city of residence"]],
  ["skills", ["skills", "key skills", "technical skills"]],
];

const AUTOCOMPLETE: Record<string, FieldIdentity> = {
  "given-name": "first_name",
  "family-name": "last_name",
  name: "full_name",
  email: "email",
  tel: "phone",
  "tel-national": "phone",
};

const TEXT_CONTROLS: ReadonlySet<ControlKind> = new Set(["text", "email", "tel", "url", "textarea"]);

type Classifiable = Pick<FieldDescriptor, "label" | "control" | "section" | "autocomplete" | "key">;

export function sensitiveCategory(field: Pick<FieldDescriptor, "label" | "section">): SensitiveCategory | null {
  if (field.section === "demographic") return "demographic";
  const label = normaliseLabel(field.label);
  for (const [category, pattern] of SENSITIVE) {
    if (pattern.test(label)) return category;
  }
  return null;
}

function identityOf(field: Classifiable): FieldIdentity {
  const label = normaliseLabel(field.label);

  if (field.control === "file") {
    if (/\bcover letter\b/.test(label) || /cover_letter/.test(field.key)) return "cover_letter";
    if (/\bresume\b|\brésumé\b|\bcv\b|\bcurriculum vitae\b/.test(label) || /\bresume\b/.test(field.key)) return "resume";
    return "unknown";
  }

  // The site's own statement of what the field is, when it makes one.
  const token = field.autocomplete?.trim().toLowerCase().split(/\s+/).at(-1) ?? "";
  const fromToken = AUTOCOMPLETE[token];
  if (fromToken) return fromToken;

  for (const [identity, labels] of EXACT_LABELS) {
    if (labels.includes(label)) return identity;
  }
  if (/^(preferred|chosen) (first )?name$|\bname you'?d prefer\b|\bnickname\b/.test(label)) return "preferred_name";
  if (/^(your )?linkedin( profile)?( url| link)?$/.test(label)) return "linkedin";
  if (/^(your )?github( profile)?( url| link| username)?$/.test(label)) return "github";
  if (/^(personal )?(website|portfolio|blog|homepage|web site)( url| link)?$/.test(label)) return "website";
  if (field.control === "email") return "email";
  if (field.control === "tel") return "phone";
  if (field.control === "textarea") return "free_text";
  return "unknown";
}

export function classifyField(field: Classifiable): Classification {
  // Sensitive first, always: it is the answer that wins over any other reading.
  const sensitive = sensitiveCategory(field);
  if (sensitive) return { identity: field.control === "file" ? identityOf(field) : "unknown", sensitive };
  return { identity: identityOf(field), sensitive: null };
}

/** Whether the filler may ever set this kind of control. Choices, files and boxes are the person's to set. */
export function isTextControl(control: ControlKind): boolean {
  return TEXT_CONTROLS.has(control);
}
