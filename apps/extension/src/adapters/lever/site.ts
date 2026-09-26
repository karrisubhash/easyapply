/**
 * Lever-hosted job pages: what both Lever adapters agree on.
 *
 * Checked against a live jobs.lever.co posting and its application page on
 * 2026-09-26: a company's board is /<company>, a posting is
 * /<company>/<posting uuid>, and its application form is a page of its own at
 * /<company>/<posting uuid>/apply — unlike Greenhouse, where the form sits
 * beside the posting.
 *
 * Only jobs.lever.co. Lever runs other hosts (an EU one among them) that were
 * not checked, so they are not claimed.
 */

export const SITE_NAME = "Lever";

export const HOST = "jobs.lever.co";

export const MATCHES: readonly string[] = ["https://jobs.lever.co/*"];

const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const POSTING_PATH = new RegExp(`^/([^/]+)/(${UUID})/?$`, "i");
const APPLY_PATH = new RegExp(`^/([^/]+)/(${UUID})/apply/?$`, "i");

export function isLeverUrl(url: URL): boolean {
  return url.protocol === "https:" && url.hostname === HOST;
}

/** The posting's id when this URL is a posting page; otherwise null. */
export function postingId(url: URL): string | null {
  return POSTING_PATH.exec(url.pathname)?.[2]?.toLowerCase() ?? null;
}

/** The posting's own URL when this URL is its application page; otherwise null. */
export function postingOfApplication(url: URL): string | null {
  const match = APPLY_PATH.exec(url.pathname);
  return match ? `${url.origin}/${match[1]}/${match[2]}` : null;
}
