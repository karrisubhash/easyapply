/**
 * Lever-hosted job postings: jobs.lever.co/<company>/<uuid>.
 *
 * What is read, and from where (a live posting, 2026-09-26; the structure is in
 * test/fixtures/lever-job.html):
 *
 *   job_title        `.posting-headline h2`
 *   location_raw     `.posting-categories .location`
 *   description_text every `.section.page-centered` below the header — the
 *                    job-description section, the h3 + requirements-list
 *                    sections, the closing section — without the Apply block
 *   company_name     the document title, "<company> - <title>"; else the
 *                    header logo's alt text, "<company> logo"
 *   external_job_id  the posting's uuid, from the path
 *
 * The application page (/apply) is reported as such, not read as a posting:
 * it carries the title but not the description, and saving from it would save
 * a posting with nothing for the structurer to read.
 */

import { blockText, cleanLabel, labelText } from "../../extraction/text";
import type { ExtractedField, ExtractionResult, ExtractionWarning, SiteAdapter } from "../../extraction/types";
import { isLeverUrl, MATCHES, postingId, postingOfApplication, SITE_NAME } from "./site";

const LOGO_SUFFIX = /\s+logo$/i;

const FIELD_NAMES: Record<ExtractedField, string> = {
  job_title: "job title",
  company_name: "company",
  location_raw: "location",
  description_text: "job description",
  external_job_id: "job id",
};

/** "<company> - <title>", stripped of the known title rather than split on the first dash. */
function companyFromDocumentTitle(documentTitle: string, jobTitle: string): string | null {
  const title = cleanLabel(documentTitle);
  const suffix = ` - ${jobTitle}`;
  if (jobTitle && title.endsWith(suffix)) return cleanLabel(title.slice(0, -suffix.length)) || null;
  return null;
}

function companyFromLogo(document: Document): string | null {
  const alt = document.querySelector(".main-header-logo img")?.getAttribute("alt");
  if (!alt || !LOGO_SUFFIX.test(alt)) return null;
  return cleanLabel(alt.replace(LOGO_SUFFIX, "")) || null;
}

function description(document: Document): string {
  const sections = [...document.querySelectorAll(".posting-page .section.page-centered")].filter(
    (section) => !section.matches(".posting-header, .last-section-apply") && !section.querySelector("form"),
  );
  return sections
    .map((section) => blockText(section))
    .filter(Boolean)
    .join("\n\n");
}

function listFields(fields: ExtractedField[]): string {
  const names = fields.map((field) => FIELD_NAMES[field]);
  return names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export const lever: SiteAdapter = {
  id: "lever",
  siteName: SITE_NAME,
  sourceType: "lever",
  matches: MATCHES,

  handles: isLeverUrl,

  extract(document: Document, url: URL): ExtractionResult {
    if (!isLeverUrl(url)) return { status: "unsupported", message: "This is not a Lever job page." };

    const posting = postingOfApplication(url);
    if (posting) {
      return {
        status: "application_page",
        site: SITE_NAME,
        postingUrl: posting,
        message: "This is the application form for a Lever posting. EasyApply saves a job from its posting page; the form here can still be prepared.",
      };
    }

    const id = postingId(url);
    if (id === null) {
      return {
        status: "not_job_page",
        site: SITE_NAME,
        message: "This is a Lever job board, not a single job posting. Open one posting to save it.",
      };
    }

    const jobTitle = labelText(document.querySelector(".posting-headline h2"));
    const text = description(document);
    const location = labelText(document.querySelector(".posting-categories .location"));
    const company = companyFromDocumentTitle(document.title, jobTitle) ?? companyFromLogo(document);

    const missing: ExtractedField[] = [];
    if (!jobTitle) missing.push("job_title");
    if (!text) missing.push("description_text");
    if (missing.length > 0) {
      return {
        status: "failed",
        site: SITE_NAME,
        missing,
        message:
          `EasyApply could not find the ${listFields(missing)} on this Lever page. ` +
          "It may still be loading, or Lever may have changed its layout. Nothing has been saved.",
      };
    }

    const warnings: ExtractionWarning[] = [];
    if (!company) warnings.push({ field: "company_name", message: "The company name was not found on the page." });
    if (!location) warnings.push({ field: "location_raw", message: "The location was not found on the page." });

    return {
      status: "extracted",
      site: SITE_NAME,
      job: {
        source_type: "lever",
        source_url: `${url.origin}${url.pathname}${url.search}`,
        external_job_id: id,
        job_title: jobTitle,
        company_name: company,
        location_raw: location || null,
        description_text: text,
      },
      warnings,
    };
  },
};
