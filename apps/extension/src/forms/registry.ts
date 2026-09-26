/**
 * The application forms this extension can read, and the one entry point for
 * reading one.
 *
 * Two sites — the form Greenhouse renders beside its postings, and Lever's
 * application page. A site's form adapter sits beside its posting adapter
 * (src/adapters/<site>/), sharing that site's host and path rules, and is
 * listed here.
 */

import { greenhouseApplication } from "../adapters/greenhouse/application";
import { leverApplication } from "../adapters/lever/application";
import type { FormAdapter, FormDetection } from "./types";

export const FORM_ADAPTERS: readonly FormAdapter[] = [greenhouseApplication, leverApplication];

function parseUrl(href: string): URL | null {
  try {
    return new URL(href);
  } catch {
    return null;
  }
}

export function findFormAdapter(href: string): FormAdapter | null {
  const url = parseUrl(href);
  return url ? (FORM_ADAPTERS.find((adapter) => adapter.handles(url)) ?? null) : null;
}

/** Read the application form on a page, or say precisely why not. Never throws. */
export function detectForm(document: Document, href: string): FormDetection {
  const url = parseUrl(href);
  const adapter = url ? FORM_ADAPTERS.find((candidate) => candidate.handles(url)) : undefined;
  if (!url || !adapter) {
    return { status: "unsupported", message: "EasyApply can't read application forms on this page yet." };
  }
  try {
    return adapter.detect(document, url);
  } catch {
    return {
      status: "no_form",
      site: adapter.siteName,
      message: `Reading this ${adapter.siteName} application form failed unexpectedly. Nothing was filled in.`,
    };
  }
}
