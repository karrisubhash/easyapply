/**
 * From an application form's fields to proposed answers, out of the person's
 * own confirmed profile — with how sure each one is, and why.
 *
 *   field ─▶ classify (src/forms/classify.ts) ─▶ profile fact ─▶ proposal
 *
 * The profile is EasyApply's canonical one — the current confirmed version's
 * snapshot, exactly as the API returns it (`SnapshotRead`); there is no second
 * profile schema here and nothing is stored. Every proposal is one of:
 *
 *   exact    the profile's own value, copied verbatim, into a field whose
 *            meaning is certain: email, phone, full name, location, the one
 *            LinkedIn link. Ticked for filling by default.
 *   derived  the profile's value, transformed by a rule that can be wrong for
 *            some people: a first name split from a two-word full name, a
 *            GitHub link that points at a repository, a website picked out of
 *            the links. Shown with the rule, and ticked only when the rule
 *            cannot plausibly misfire (the two-word name).
 *   none     nothing proposed. Sensitive questions, questions the profile has
 *            no fact for, free-text answers, choices, files. Each says why and
 *            what the person should do.
 *
 * Nothing is inferred beyond that. A three-word name is not split. Years of
 * experience are not added up from dated jobs. A location is not turned into
 * a yes to "are you based in Berlin?". Unknown fields stay unknown.
 */

import type { LinkItem, Snapshot } from "@easyapply/shared";

import { classifyField, isTextControl, SENSITIVE_LABELS, type Classification, type FieldIdentity } from "../forms/classify";
import type { FieldDescriptor } from "../forms/types";

export type Confidence = "exact" | "derived" | "none";

/** What the person needs to do about a field. */
export type ProposalAction =
  /** A value is proposed; the person ticks or unticks it. */
  | "fill"
  /** Theirs to answer on the page: sensitive, unknown, a choice, or nothing in the profile. */
  | "review"
  /** A file they attach themselves. */
  | "attach"
  /** Already holds something; left as it is. */
  | "leave";

export interface Proposal {
  field: FieldDescriptor;
  classification: Classification;
  action: ProposalAction;
  confidence: Confidence;
  /** The value to fill, when one is proposed. */
  value: string | null;
  /** Where it came from, in words, e.g. "your profile's email address". */
  source: string | null;
  /** Ticked for filling before the person touches anything. */
  preselected: boolean;
  /** Why this proposal is what it is — shown beside the field. */
  note: string;
  /** Something from the profile the person may enter themselves, never filled: e.g. their location, for a location picker. */
  hint: string | null;
}

interface Found {
  value: string | null;
  confidence: Confidence;
  source: string | null;
  preselected: boolean;
  /** Why there is no value, or what the rule was. */
  note: string;
}

const NOTHING = (note: string): Found => ({ value: null, confidence: "none", source: null, preselected: false, note });

function present(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function hostOf(link: LinkItem): string | null {
  try {
    return new URL(link.url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function onSite(link: LinkItem, site: string): boolean {
  const host = hostOf(link);
  return host !== null && (host === site || host.endsWith(`.${site}`));
}

function words(name: string): string[] {
  return name.split(/\s+/).filter(Boolean);
}

function nameParts(snapshot: Snapshot, part: "first" | "last"): Found {
  const name = present(snapshot.contact?.full_name);
  if (!name) return NOTHING("Your profile doesn't have your name.");
  const parts = words(name);
  if (parts.length === 2) {
    const value = part === "first" ? parts[0]! : parts[1]!;
    return {
      value,
      confidence: "derived",
      source: `the ${part} word of your profile's name, "${name}"`,
      preselected: true,
      note: `Split from your profile's name, "${name}". Check it's right.`,
    };
  }
  return NOTHING(
    `Your profile stores your name as one line ("${name}"), and which part is your ${part} name isn't something EasyApply can know. Type it yourself.`,
  );
}

function oneLink(snapshot: Snapshot, site: "linkedin.com" | "github.com", what: string): Found {
  const links = snapshot.links.filter((link) => onSite(link, site));
  if (links.length === 0) return NOTHING(`Your profile doesn't have a ${what} link.`);
  if (links.length > 1) return NOTHING(`Your profile has ${links.length} ${what} links, so which one to use is yours to choose.`);
  const link = links[0]!;
  if (site === "github.com") {
    let path: string[] = [];
    try {
      path = new URL(link.url).pathname.split("/").filter(Boolean);
    } catch {
      path = [];
    }
    if (path.length !== 1) {
      return {
        value: link.url,
        confidence: "derived",
        source: "your profile's GitHub link",
        preselected: false,
        note: "Your GitHub link points at a repository rather than a profile. Tick it if it's what you want to share.",
      };
    }
  }
  return { value: link.url, confidence: "exact", source: `your profile's ${what} link`, preselected: true, note: `From your profile's ${what} link.` };
}

const WEBSITE_LABEL = /\b(website|portfolio|personal|homepage|home page|blog|site)\b/i;

function website(snapshot: Snapshot): Found {
  const others = snapshot.links.filter((link) => !onSite(link, "linkedin.com") && !onSite(link, "github.com"));
  if (others.length === 0) return NOTHING("Your profile doesn't have a website link.");
  const labelled = others.filter((link) => WEBSITE_LABEL.test(link.label ?? ""));
  const pick = others.length === 1 ? others[0]! : labelled.length === 1 ? labelled[0]! : null;
  if (!pick) return NOTHING(`Your profile has ${others.length} links besides LinkedIn and GitHub, so which one is your website is yours to choose.`);
  const certain = WEBSITE_LABEL.test(pick.label ?? "");
  return {
    value: pick.url,
    confidence: certain ? "exact" : "derived",
    source: `your profile's link${pick.label ? ` labelled "${pick.label}"` : ""}`,
    preselected: certain,
    note: certain
      ? `From your profile's "${pick.label}" link.`
      : "Your profile's only link besides LinkedIn and GitHub. Tick it if it's your website.",
  };
}

function valueFor(identity: FieldIdentity, snapshot: Snapshot): Found {
  const contact = snapshot.contact;
  switch (identity) {
    case "email": {
      const value = present(contact?.email);
      return value
        ? { value, confidence: "exact", source: "your profile's email address", preselected: true, note: "From your profile's email address." }
        : NOTHING("Your profile doesn't have an email address.");
    }
    case "phone": {
      const value = present(contact?.phone);
      return value
        ? { value, confidence: "exact", source: "your profile's phone number", preselected: true, note: "From your profile's phone number, as you entered it." }
        : NOTHING("Your profile doesn't have a phone number.");
    }
    case "full_name": {
      const value = present(contact?.full_name);
      return value
        ? { value, confidence: "exact", source: "your profile's name", preselected: true, note: "From your profile's name." }
        : NOTHING("Your profile doesn't have your name.");
    }
    case "first_name":
      return nameParts(snapshot, "first");
    case "last_name":
      return nameParts(snapshot, "last");
    case "preferred_name":
      return NOTHING("EasyApply doesn't keep a preferred name. Type one yourself if you use one.");
    case "location": {
      const value = present(contact?.location);
      return value
        ? { value, confidence: "exact", source: "your profile's location", preselected: true, note: "From your profile's location." }
        : NOTHING("Your profile doesn't have a location.");
    }
    case "city": {
      const value = present(contact?.location);
      return value
        ? {
            value,
            confidence: "derived",
            source: "your profile's location",
            preselected: false,
            note: "This asks for a city; your profile's location is the closest thing it has. Tick it if it fits.",
          }
        : NOTHING("Your profile doesn't have a location.");
    }
    case "linkedin":
      return oneLink(snapshot, "linkedin.com", "LinkedIn");
    case "github":
      return oneLink(snapshot, "github.com", "GitHub");
    case "website":
      return website(snapshot);
    case "skills": {
      const names = snapshot.skills.map((skill) => skill.name.trim()).filter(Boolean);
      return names.length > 0
        ? {
            value: names.join(", "),
            confidence: "derived",
            source: "your profile's skills, as a list",
            preselected: false,
            note: "Your profile's skills, comma-separated. Tick it if the field wants a list like this.",
          }
        : NOTHING("Your profile doesn't list any skills.");
    }
    case "free_text":
      return NOTHING("This question needs your own answer. EasyApply doesn't write answers to application questions.");
    case "resume":
    case "cover_letter":
    case "unknown":
      return NOTHING("Your profile doesn't have enough information to answer this field. It requires your review.");
  }
}

export function propose(field: FieldDescriptor, snapshot: Snapshot): Proposal {
  const classification = classifyField(field);
  const base = { field, classification, value: null, confidence: "none" as const, source: null, preselected: false, hint: null };

  if (classification.sensitive) {
    return {
      ...base,
      action: "review",
      note: `This asks about ${SENSITIVE_LABELS[classification.sensitive]}. EasyApply never answers that for you — review it and answer it yourself.`,
    };
  }
  if (field.control === "file") {
    return {
      ...base,
      action: "attach",
      note:
        classification.identity === "resume"
          ? "Attach your resume yourself. EasyApply doesn't upload files into job sites."
          : classification.identity === "cover_letter"
            ? "Attach a cover letter yourself if you want to send one."
            : "Attach this yourself if it applies to you.",
    };
  }
  if (field.filled) {
    return { ...base, action: "leave", note: "Already filled in on the page. EasyApply leaves it as it is." };
  }

  const found = valueFor(classification.identity, snapshot);

  if (!isTextControl(field.control) || !field.settable) {
    // A choice — a picker, a checkbox, an option list — is the person's to make.
    return {
      ...base,
      action: "review",
      hint: found.value,
      note: found.value
        ? "Choose this yourself: EasyApply doesn't pick options on job sites."
        : isTextControl(field.control)
          ? "This field can't be filled from here. It requires your review."
          : "This question requires your review.",
    };
  }
  if (found.value === null) {
    return { ...base, action: "review", note: found.note };
  }
  if (field.maxLength !== null && Array.from(found.value).length > field.maxLength) {
    return {
      ...base,
      action: "review",
      hint: found.value,
      note: `Your profile's value is longer than this field allows (${field.maxLength} characters). EasyApply won't cut it to fit.`,
    };
  }
  return {
    field,
    classification,
    action: "fill",
    confidence: found.confidence,
    value: found.value,
    source: found.source,
    preselected: found.preselected,
    note: found.note,
    hint: null,
  };
}

export function proposeAll(fields: readonly FieldDescriptor[], snapshot: Snapshot): Proposal[] {
  return fields.map((field) => propose(field, snapshot));
}
