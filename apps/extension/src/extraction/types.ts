/**
 * What reading a page can produce.
 *
 * Every outcome is a value, never an exception: the popup has to say something
 * specific in each case, and "the page could not be read" is four different
 * sentences depending on why.
 */

import type { JobSourceType } from "../contract";

/** The fields an adapter reads off the page. Names are the API's own. */
export type ExtractedField =
  | "job_title"
  | "company_name"
  | "location_raw"
  | "description_text"
  | "external_job_id";

/**
 * A posting as read from the page, before the person has reviewed it.
 *
 * Deliberately has no user, no status and no processing state — nothing a page
 * could be made to assert about who is saving it or what EasyApply thinks of it.
 */
export interface ExtractedJob {
  source_type: JobSourceType;
  source_url: string;
  external_job_id: string | null;
  job_title: string;
  company_name: string | null;
  location_raw: string | null;
  description_text: string;
}

/** Something the person should know before saving, that does not stop them. */
export interface ExtractionWarning {
  field: ExtractedField;
  message: string;
}

export type ExtractionResult =
  /** Everything required was found. Optional fields may still be missing. */
  | {
      status: "extracted";
      site: string;
      job: ExtractedJob;
      warnings: ExtractionWarning[];
    }
  /** A supported site, but this page is not a single posting (a board, a search). */
  | { status: "not_job_page"; site: string; message: string }
  /**
   * The application form of a posting, on a page of its own (Lever's /apply).
   * Nothing to save here — the posting is at `postingUrl` — but the form can
   * still be prepared.
   */
  | { status: "application_page"; site: string; postingUrl: string; message: string }
  /** A posting page on a supported site whose required fields could not be found. */
  | { status: "failed"; site: string; missing: ExtractedField[]; message: string }
  /** No adapter claims this page. */
  | { status: "unsupported"; message: string };

/**
 * One job site.
 *
 * Adding a site is adding one of these to `registry.ts` (and its hosts to the
 * manifest's content-script matches, which test/manifest.test.ts checks). An
 * adapter only reads: it is handed the document and must not change it.
 */
export interface SiteAdapter {
  /** Stable identifier, used in logs and tests. */
  readonly id: string;
  /** What the person is told the site is. */
  readonly siteName: string;
  /** The `source_type` the API is sent. A closed set the API defines. */
  readonly sourceType: JobSourceType;
  /** Content-script match patterns for this site's pages. */
  readonly matches: readonly string[];
  /** Whether this URL is on this adapter's site at all. */
  handles(url: URL): boolean;
  /** Read the page. Pure: the same document and URL give the same result. */
  extract(document: Document, url: URL): ExtractionResult;
}
