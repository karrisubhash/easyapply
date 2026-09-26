import type { Profile, ProfileVersion, Snapshot } from "@easyapply/shared";

export const emptySnapshot: Snapshot = {
  contact: null,
  summary: null,
  links: [],
  experiences: [],
  education: [],
  skills: [],
  projects: [],
  certifications: [],
};

export const filledSnapshot: Snapshot = {
  contact: {
    full_name: "K Subhash",
    email: "subhash@example.com",
    phone: "+91 6301998030",
    location: "Visakhapatnam, India",
  },
  summary:
    "Software developer focused on backend engineering and AI/ML, with hands-on " +
    "experience building Django applications and REST APIs.",
  links: [
    { url: "https://github.com/example", label: "GitHub" },
    { url: "https://www.linkedin.com/in/example/", label: "LinkedIn" },
  ],
  experiences: [
    {
      company: "APTRANSCO",
      job_title: "AI Developer Intern",
      employment_type: "internship",
      location: "Vijayawada",
      start_date: "2026-05-15",
      start_date_precision: "day",
      end_date: "2026-08-15",
      end_date_precision: "day",
      description: null,
    },
  ],
  education: [
    {
      institution: "Anil Neerukonda Institute of Technology & Sciences",
      degree: "B.Tech",
      field_of_study: "Mechanical Engineering",
      start_date: null,
      start_date_precision: null,
      end_date: null,
      end_date_precision: null,
      grade: null,
      description: null,
    },
  ],
  skills: [
    {
      name: "Django",
      category: "Backend",
      proficiency: "intermediate",
      years_of_experience: null,
    },
    { name: "Python", category: "Programming", proficiency: "advanced", years_of_experience: null },
  ],
  projects: [
    {
      name: "EasyApply",
      role: null,
      description: null,
      start_date: null,
      start_date_precision: null,
      end_date: null,
      end_date_precision: null,
      project_url: null,
      technologies: ["FastAPI", "PostgreSQL"],
      links: [{ url: "https://github.com/example/easyapply", label: "Repository" }],
    },
  ],
  certifications: [
    {
      name: "AZ-900",
      issuing_organization: "Microsoft",
      issue_date: null,
      expiration_date: null,
      credential_id: null,
      credential_url: null,
    },
  ],
};

export const profile: Profile = {
  id: 1,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
};

export function draftVersion(overrides: Partial<ProfileVersion> = {}): ProfileVersion {
  return {
    // The database id, which is what the match and tailoring endpoints take.
    // Deliberately not equal to `version_number`: they are different numbers on
    // the same row, and a fixture where they happen to match would let a test
    // pass with the wrong one being sent.
    id: 101,
    version_number: 1,
    status: "draft",
    is_current: false,
    created_by_type: "user",
    created_at: "2026-09-02T10:00:00Z",
    confirmed_at: null,
    snapshot: emptySnapshot,
    ...overrides,
  };
}

export function confirmedVersion(overrides: Partial<ProfileVersion> = {}): ProfileVersion {
  return {
    id: 102,
    version_number: 1,
    status: "confirmed",
    is_current: true,
    created_by_type: "user",
    created_at: "2026-09-02T10:00:00Z",
    confirmed_at: "2026-09-03T12:00:00Z",
    snapshot: filledSnapshot,
    ...overrides,
  };
}
