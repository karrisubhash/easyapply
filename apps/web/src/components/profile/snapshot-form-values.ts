import type { DatePrecision, Snapshot, SnapshotInput } from "@easyapply/shared";

type ExperienceIn = NonNullable<SnapshotInput["experiences"]>[number];
type EducationIn = NonNullable<SnapshotInput["education"]>[number];
type SkillIn = NonNullable<SnapshotInput["skills"]>[number];
type ProjectIn = NonNullable<SnapshotInput["projects"]>[number];
type CertificationIn = NonNullable<SnapshotInput["certifications"]>[number];
type LinkIn = NonNullable<SnapshotInput["links"]>[number];

/**
 * The form's own shape.
 *
 * HTML inputs produce strings, never null, so the form carries strings
 * throughout and converts at the boundary. Keeping that conversion in one
 * tested pair of functions is what stops empty-string-versus-null bugs from
 * being scattered across field handlers.
 */

export interface ContactForm {
  full_name: string;
  email: string;
  phone: string;
  location: string;
}

export interface LinkForm {
  url: string;
  label: string;
}

export interface ExperienceForm {
  company: string;
  job_title: string;
  employment_type: string;
  location: string;
  start_date: string;
  start_date_precision: DatePrecision;
  end_date: string;
  end_date_precision: DatePrecision;
  description: string;
}

export interface EducationForm {
  institution: string;
  degree: string;
  field_of_study: string;
  start_date: string;
  start_date_precision: DatePrecision;
  end_date: string;
  end_date_precision: DatePrecision;
  grade: string;
  description: string;
}

export interface SkillForm {
  name: string;
  category: string;
  proficiency: string;
  years_of_experience: string;
}

export interface ProjectForm {
  name: string;
  role: string;
  description: string;
  start_date: string;
  start_date_precision: DatePrecision;
  end_date: string;
  end_date_precision: DatePrecision;
  project_url: string;
  technologies: string;
  /**
   * The project's own links (a repository, a demo), one per line: a label,
   * then the address — or the address alone. Carried through the form because
   * a save REPLACES the snapshot: a form that did not hold them would erase
   * every project link the resume reader found the first time a draft was
   * edited.
   */
  links: string;
}

export interface CertificationForm {
  name: string;
  issuing_organization: string;
  issue_date: string;
  expiration_date: string;
  credential_id: string;
  credential_url: string;
}

export interface SnapshotFormValues {
  contact: ContactForm;
  summary: string;
  links: LinkForm[];
  experiences: ExperienceForm[];
  education: EducationForm[];
  skills: SkillForm[];
  projects: ProjectForm[];
  certifications: CertificationForm[];
}

export const EMPTY_CONTACT: ContactForm = {
  full_name: "",
  email: "",
  phone: "",
  location: "",
};

export const EMPTY_LINK: LinkForm = {
  url: "",
  label: "",
};

export const EMPTY_EXPERIENCE: ExperienceForm = {
  company: "",
  job_title: "",
  employment_type: "",
  location: "",
  start_date: "",
  start_date_precision: "day",
  end_date: "",
  end_date_precision: "day",
  description: "",
};

export const EMPTY_EDUCATION: EducationForm = {
  institution: "",
  degree: "",
  field_of_study: "",
  start_date: "",
  start_date_precision: "day",
  end_date: "",
  end_date_precision: "day",
  grade: "",
  description: "",
};

export const EMPTY_SKILL: SkillForm = {
  name: "",
  category: "",
  proficiency: "",
  years_of_experience: "",
};

export const EMPTY_PROJECT: ProjectForm = {
  name: "",
  role: "",
  description: "",
  start_date: "",
  start_date_precision: "day",
  end_date: "",
  end_date_precision: "day",
  project_url: "",
  technologies: "",
  links: "",
};

export const EMPTY_CERTIFICATION: CertificationForm = {
  name: "",
  issuing_organization: "",
  issue_date: "",
  expiration_date: "",
  credential_id: "",
  credential_url: "",
};

/** "" means "not provided", which the API spells as null. */
function text(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** A date and its precision travel together or not at all — the API enforces it. */
function datePair(
  value: string,
  precision: DatePrecision,
): { date: string | null; precision: DatePrecision | null } {
  return value ? { date: value, precision } : { date: null, precision: null };
}

/**
 * A contact record with nothing in it is absent, not blank.
 *
 * Sending four nulls would write an empty contact row over whatever the resume
 * read, and would make the view render a contact block with nothing in it.
 */
function contact(values: ContactForm): SnapshotInput["contact"] {
  const record = {
    full_name: text(values.full_name),
    email: text(values.email),
    phone: text(values.phone),
    location: text(values.location),
  };
  return Object.values(record).some(Boolean) ? record : null;
}

/** One link per line: `label address`, or the address alone. */
export function projectLinksToText(links: { url: string; label?: string | null }[]): string {
  return links.map((link) => (link.label ? `${link.label} ${link.url}` : link.url)).join("\n");
}

/**
 * The inverse of `projectLinksToText`. The address is the line's LAST word —
 * split at any whitespace, a pasted tab included — because a label may have
 * spaces in it and an address may not; a blank line is not a link.
 */
export function projectLinksFromText(value: string): LinkIn[] {
  return linkLines(value).map((line) => {
    const parts = /^(.*?)\s+(\S+)$/.exec(line);
    if (!parts) return { url: line, label: null };
    return { url: parts[2], label: parts[1] || null };
  });
}

function linkLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

// A scheme ("https:", "mailto:"), "www.", or a bare host with a dot — the forms
// resume extraction stores. Only the shape is checked, not reachability.
const ADDRESS = /^(?:[a-z][a-z0-9+.-]*:\S+|www\.\S+|[^\s/:]+\.[a-z]{2,}(?:[/?#]\S*)?)$/i;

/**
 * Why a project-links field would save something that is not a link, or
 * undefined. The API only bounds the length, so "GitHub" alone — or an address
 * typed before its label — was stored as the address and rendered as a link
 * to nowhere.
 */
export function projectLinksProblem(value: string): string | undefined {
  const lines = linkLines(value);
  const bad = lines.findIndex((line) => !ADDRESS.test(line.split(/\s+/).pop() ?? ""));
  if (bad === -1) return undefined;
  const which = lines.length > 1 ? `Line ${bad + 1}` : "This line";
  return `${which} does not end with a web address. Write the label first, then the address.`;
}

export function toSnapshotInput(values: SnapshotFormValues): SnapshotInput {
  return {
    contact: contact(values.contact),
    summary: text(values.summary),
    // A row the user added and left blank is not a link. Dropping it here keeps
    // the empty row out of the profile without the form having to police it.
    links: values.links
      .filter((item) => item.url.trim() !== "")
      .map((item): LinkIn => ({
        url: item.url.trim(),
        label: text(item.label),
      })),
    experiences: values.experiences.map((item): ExperienceIn => {
      const start = datePair(item.start_date, item.start_date_precision);
      const end = datePair(item.end_date, item.end_date_precision);
      return {
        company: item.company.trim(),
        job_title: item.job_title.trim(),
        employment_type: text(item.employment_type) as ExperienceIn["employment_type"],
        location: text(item.location),
        // start_date is required by the API. Sending an empty one earns a 422,
        // which is the correct answer rather than something to paper over here.
        start_date: start.date as string,
        start_date_precision: start.precision,
        end_date: end.date,
        end_date_precision: end.precision,
        description: text(item.description),
      };
    }),
    education: values.education.map((item): EducationIn => {
      const start = datePair(item.start_date, item.start_date_precision);
      const end = datePair(item.end_date, item.end_date_precision);
      return {
        institution: item.institution.trim(),
        degree: text(item.degree),
        field_of_study: text(item.field_of_study),
        start_date: start.date,
        start_date_precision: start.precision,
        end_date: end.date,
        end_date_precision: end.precision,
        grade: text(item.grade),
        description: text(item.description),
      };
    }),
    skills: values.skills.map((item): SkillIn => ({
      name: item.name.trim(),
      category: text(item.category),
      proficiency: text(item.proficiency) as SkillIn["proficiency"],
      years_of_experience: item.years_of_experience === "" ? null : Number(item.years_of_experience),
    })),
    projects: values.projects.map((item): ProjectIn => {
      const start = datePair(item.start_date, item.start_date_precision);
      const end = datePair(item.end_date, item.end_date_precision);
      return {
        name: item.name.trim(),
        role: text(item.role),
        description: text(item.description),
        start_date: start.date,
        start_date_precision: start.precision,
        end_date: end.date,
        end_date_precision: end.precision,
        project_url: text(item.project_url),
        technologies: item.technologies
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean),
        links: projectLinksFromText(item.links),
      };
    }),
    certifications: values.certifications.map((item): CertificationIn => ({
      name: item.name.trim(),
      issuing_organization: text(item.issuing_organization),
      issue_date: text(item.issue_date),
      expiration_date: text(item.expiration_date),
      credential_id: text(item.credential_id),
      credential_url: text(item.credential_url),
    })),
  };
}

export function toFormValues(snapshot: Snapshot): SnapshotFormValues {
  return {
    contact: {
      full_name: snapshot.contact?.full_name ?? "",
      email: snapshot.contact?.email ?? "",
      phone: snapshot.contact?.phone ?? "",
      location: snapshot.contact?.location ?? "",
    },
    summary: snapshot.summary ?? "",
    links: snapshot.links.map((item) => ({
      url: item.url,
      label: item.label ?? "",
    })),
    experiences: snapshot.experiences.map((item) => ({
      company: item.company,
      job_title: item.job_title,
      employment_type: item.employment_type ?? "",
      location: item.location ?? "",
      start_date: item.start_date ?? "",
      start_date_precision: item.start_date_precision ?? "day",
      end_date: item.end_date ?? "",
      end_date_precision: item.end_date_precision ?? "day",
      description: item.description ?? "",
    })),
    education: snapshot.education.map((item) => ({
      institution: item.institution,
      degree: item.degree ?? "",
      field_of_study: item.field_of_study ?? "",
      start_date: item.start_date ?? "",
      start_date_precision: item.start_date_precision ?? "day",
      end_date: item.end_date ?? "",
      end_date_precision: item.end_date_precision ?? "day",
      grade: item.grade ?? "",
      description: item.description ?? "",
    })),
    skills: snapshot.skills.map((item) => ({
      name: item.name,
      category: item.category ?? "",
      proficiency: item.proficiency ?? "",
      years_of_experience:
        item.years_of_experience === null || item.years_of_experience === undefined
          ? ""
          : String(item.years_of_experience),
    })),
    projects: snapshot.projects.map((item) => ({
      name: item.name,
      role: item.role ?? "",
      description: item.description ?? "",
      start_date: item.start_date ?? "",
      start_date_precision: item.start_date_precision ?? "day",
      end_date: item.end_date ?? "",
      end_date_precision: item.end_date_precision ?? "day",
      project_url: item.project_url ?? "",
      technologies: item.technologies.join(", "),
      links: projectLinksToText(item.links ?? []),
    })),
    certifications: snapshot.certifications.map((item) => ({
      name: item.name,
      issuing_organization: item.issuing_organization ?? "",
      issue_date: item.issue_date ?? "",
      expiration_date: item.expiration_date ?? "",
      credential_id: item.credential_id ?? "",
      credential_url: item.credential_url ?? "",
    })),
  };
}
