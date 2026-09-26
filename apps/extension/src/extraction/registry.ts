/**
 * The job sites this extension can read, and the one entry point for reading.
 *
 * Two sites: Greenhouse and Lever. Adding another is an adapter in
 * `src/adapters/<site>/`, a line in ADAPTERS, and its match patterns in
 * src/manifest.json — the manifest test fails until the last two agree.
 */

import { greenhouse } from "../adapters/greenhouse/job";
import { lever } from "../adapters/lever/job";
import type { ExtractionResult, SiteAdapter } from "./types";

export const ADAPTERS: readonly SiteAdapter[] = [greenhouse, lever];

export function findAdapter(url: URL): SiteAdapter | null {
  return ADAPTERS.find((adapter) => adapter.handles(url)) ?? null;
}

/** Every supported site's name, for telling the person what does work. */
export function supportedSites(): string {
  return ADAPTERS.map((adapter) => adapter.siteName).join(", ");
}

function parseUrl(href: string): URL | null {
  try {
    return new URL(href);
  } catch {
    return null;
  }
}

/**
 * Read the job on a page, or say precisely why not.
 *
 * Never throws. An adapter that does — a selector engine bug, a page that
 * changed shape in a way nobody anticipated — is reported as a failure of that
 * site's reading, not as a crash the popup has to interpret.
 */
export function extractJob(document: Document, href: string): ExtractionResult {
  const url = parseUrl(href);
  const adapter = url ? findAdapter(url) : null;
  if (!url || !adapter) {
    return {
      status: "unsupported",
      message: `EasyApply can't read jobs on this page yet. Supported: ${supportedSites()} job boards.`,
    };
  }
  try {
    return adapter.extract(document, url);
  } catch {
    return {
      status: "failed",
      site: adapter.siteName,
      missing: [],
      message: `Reading this ${adapter.siteName} page failed unexpectedly. Nothing has been saved.`,
    };
  }
}
