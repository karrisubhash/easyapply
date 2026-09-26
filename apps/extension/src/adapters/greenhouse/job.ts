/**
 * Greenhouse-hosted job boards: job-boards.greenhouse.io and its EU twin
 * (hosts and the posting path: ./site.ts, shared with ./application.ts).
 *
 * Why this site first. The posting is server-rendered, so it is in the HTML
 * before any script runs; the page is public, so nothing about the person's own
 * account is on it; the posting's id is in its URL; and every company on it
 * shares one template, so this adapter is honestly "Greenhouse" rather than
 * "the companies it happened to be tried on". The template was checked against
 * live postings from GitLab, Figma, Discord and Cloudflare on 2026-09-26, and
 * the legacy boards.greenhouse.io host now redirects here.
 *
 * Not supported: company career sites that embed a Greenhouse board in an
 * iframe (careers.example.com/?gh_jid=…). The page there is the company's, not
 * Greenhouse's, and reading a cross-origin frame is a different adapter.
 *
 * What is read, and from where:
 *
 *   job_title        `.job__title h1`
 *   location_raw     `.job__location` (its icon is SVG and is skipped)
 *   description_text `.job__description` — which also holds the pay-range
 *                    block, and excludes the application form beside it
 *   company_name     the document title, "Job Application for <title> at
 *                    <company>"; else the board logo's alt text, "<company>
 *                    Logo". The page has no element that is just the company.
 *   external_job_id  the numeric id in /<board>/jobs/<id>
 */

import { cleanLabel, blockText, labelText } from "../../extraction/text";
import type { ExtractedField, ExtractionResult, ExtractionWarning, SiteAdapter } from "../../extraction/types";
import { isGreenhouseUrl, MATCHES, postingId, SITE_NAME } from "./site";

const TITLE_PREFIX = "Job Application for ";
const LOGO_SUFFIX = /\s+logo$/i;

const SITE = SITE_NAME;

const FIELD_NAMES: Record<ExtractedField, string> = {
  job_title: "job title",
  company_name: "company",
  location_raw: "location",
  description_text: "job description",
  external_job_id: "job id",
};

/**
 * The company, from the document title Greenhouse writes for every posting.
 *
 * Stripped of the known title rather than split on the last " at ", so a
 * company whose own name contains " at " survives. The fallback split is only
 * for a title that does not match the heading exactly.
 */
function companyFromDocumentTitle(documentTitle: string, jobTitle: string): string | null {
  const title = cleanLabel(documentTitle);
  if (!title.startsWith(TITLE_PREFIX)) return null;
  const rest = title.slice(TITLE_PREFIX.length);

  const exact = `${jobTitle} at `;
  if (jobTitle && rest.startsWith(exact)) return cleanLabel(rest.slice(exact.length)) || null;

  const at = rest.lastIndexOf(" at ");
  return at > 0 ? cleanLabel(rest.slice(at + 4)) || null : null;
}

function companyFromLogo(document: Document): string | null {
  const alt = document.querySelector(".image-container .logo img, a.logo img")?.getAttribute("alt");
  if (!alt || !LOGO_SUFFIX.test(alt)) return null;
  return cleanLabel(alt.replace(LOGO_SUFFIX, "")) || null;
}

function handles(url: URL): boolean {
  return isGreenhouseUrl(url);
}

function listFields(fields: ExtractedField[]): string {
  const names = fields.map((field) => FIELD_NAMES[field]);
  return names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export const greenhouse: SiteAdapter = {
  id: "greenhouse",
  siteName: SITE,
  sourceType: "greenhouse",
  matches: MATCHES,

  handles,

  extract(document: Document, url: URL): ExtractionResult {
    if (!handles(url)) {
      return { status: "unsupported", message: "This is not a Greenhouse job board page." };
    }

    const id = postingId(url);
    if (id === null) {
      // Where Greenhouse sends a link to a posting that has closed.
      const closed = url.searchParams.get("error") === "true";
      return {
        status: "not_job_page",
        site: SITE,
        message: closed
          ? "Greenhouse sent you here from a posting that is closed or no longer exists, so there is no job on this page to save."
          : "This is a Greenhouse job board, not a single job posting. Open one posting to save it.",
      };
    }

    const jobTitle = labelText(document.querySelector(".job__title h1"));
    const description = blockText(document.querySelector(".job__description"));
    const location = labelText(document.querySelector(".job__location"));
    const company =
      companyFromDocumentTitle(document.title, jobTitle) ?? companyFromLogo(document);

    const missing: ExtractedField[] = [];
    if (!jobTitle) missing.push("job_title");
    if (!description) missing.push("description_text");
    if (missing.length > 0) {
      return {
        status: "failed",
        site: SITE,
        missing,
        message:
          `EasyApply could not find the ${listFields(missing)} on this Greenhouse page. ` +
          "It may still be loading, or Greenhouse may have changed its layout. Nothing has been saved.",
      };
    }

    const warnings: ExtractionWarning[] = [];
    if (!company) {
      warnings.push({ field: "company_name", message: "The company name was not found on the page." });
    }
    if (!location) {
      warnings.push({ field: "location_raw", message: "The location was not found on the page." });
    }

    return {
      status: "extracted",
      site: SITE,
      job: {
        source_type: "greenhouse",
        // Without the fragment: it is never sent to a server and is not part
        // of which page this is. Tracking parameters are the API's to strip
        // (canonicalize_url), not a second copy of that rule here.
        source_url: `${url.origin}${url.pathname}${url.search}`,
        external_job_id: id,
        job_title: jobTitle,
        company_name: company,
        location_raw: location || null,
        description_text: description,
      },
      warnings,
    };
  },
};
