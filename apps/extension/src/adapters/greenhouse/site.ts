/**
 * Greenhouse-hosted job boards: what every Greenhouse adapter agrees on.
 *
 * Two adapters read these pages — the posting (./job.ts) and the application
 * form Greenhouse renders beside it (./application.ts) — and they must never
 * disagree about which pages are Greenhouse's or which of those is a single
 * posting. So the hosts, the match patterns and the posting path live here,
 * once, rather than in each.
 *
 * Checked against live postings (GitLab, Figma, Discord, Cloudflare) on
 * 2026-09-26: every company's board shares one template on one of two hosts,
 * and a posting is always /<board>/jobs/<numeric id>. The legacy
 * boards.greenhouse.io host redirects here.
 */

export const SITE_NAME = "Greenhouse";

export const HOSTS: ReadonlySet<string> = new Set(["job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"]);

/** Content-script match patterns: the two hosts, every path. */
export const MATCHES: readonly string[] = ["https://job-boards.greenhouse.io/*", "https://job-boards.eu.greenhouse.io/*"];

/** /<board>/jobs/<id> — the only path shape that is a single posting. */
const POSTING_PATH = /^\/[^/]+\/jobs\/(\d+)\/?$/;

/** Whether this URL is on a Greenhouse-hosted board at all. */
export function isGreenhouseUrl(url: URL): boolean {
  return url.protocol === "https:" && HOSTS.has(url.hostname);
}

/** The posting's own id, when this URL is a single posting; otherwise null. */
export function postingId(url: URL): string | null {
  return POSTING_PATH.exec(url.pathname)?.[1] ?? null;
}
